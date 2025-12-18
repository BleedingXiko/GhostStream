<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { invoke } from '@tauri-apps/api/tauri';
  import Header from './components/Header.svelte';
  import StatusPanel from './components/StatusPanel.svelte';
  import JobList from './components/JobList.svelte';
  import SearchingOverlay from './components/SearchingOverlay.svelte';
  import type { ServerState, Job, Health, Capabilities } from './types';

  let serverState: ServerState = 'stopped';
  let health: Health | null = null;
  let capabilities: Capabilities | null = null;
  let jobs: Map<string, Job> = new Map();
  let clientConnected = false;
  let isGhostHub = false;
  let localIp = 'localhost';
  let serverAddress = 'localhost:8765';
  let displayAddress = 'localhost:8765';
  let ws: WebSocket | null = null;
  let healthInterval: number | null = null;

  $: jobList = Array.from(jobs.values()).sort((a, b) => b.updatedAt - a.updatedAt);
  $: activeJobs = jobList.filter(j => j.status === 'processing' || j.status === 'queued');
  $: hasActivity = jobs.size > 0;

  async function startServer() {
    serverState = 'starting';
    try {
      // Get local IP for display
      try {
        localIp = await invoke('get_local_ip');
        displayAddress = `${localIp}:8765`;
        isGhostHub = await invoke('is_ghosthub_network');
      } catch (e) {
        console.log('Could not get local IP, using localhost');
      }
      
      // Try to start server (might already be running)
      try {
        await invoke('start_ghoststream');
      } catch (e) {
        // Server might already be running, that's OK
        console.log('Start server result:', e);
      }
      
      // Go to running state immediately and connect WebSocket
      // WebSocket will retry until server is ready
      serverState = 'running';
      connectWebSocket();
    } catch (e) {
      console.error('Failed to start server:', e);
      serverState = 'error';
    }
  }

  async function stopServer() {
    serverState = 'stopping';
    stopHealthPolling();
    disconnectWebSocket();
    try {
      await invoke('stop_ghoststream');
      serverState = 'stopped';
      health = null;
      capabilities = null;
      jobs.clear();
      jobs = jobs;
      clientConnected = false;
    } catch (e) {
      console.error('Failed to stop server:', e);
      serverState = 'error';
    }
  }

  async function pollHealth() {
    try {
      const healthJson: string = await invoke('check_server_health');
      health = JSON.parse(healthJson) as Health;
      if (serverState === 'starting') serverState = 'running';
    } catch (e) {
      if (serverState === 'running') {
        health = null;
      }
    }
  }

  async function fetchCapabilities() {
    // Retry quickly until capabilities are available
    for (let i = 0; i < 30; i++) {
      try {
        const capsJson: string = await invoke('get_capabilities');
        capabilities = JSON.parse(capsJson) as Capabilities;
        return;
      } catch (e) {
        await new Promise(r => setTimeout(r, 200)); // Retry every 200ms
      }
    }
  }

  function connectWebSocket() {
    if (ws) ws.close();
    
    ws = new WebSocket(`ws://${serverAddress}/ws/progress`);
    
    ws.onopen = () => {
      console.log('WebSocket connected');
      fetchCapabilities();
    };

    ws.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      
      if (msg.type === 'ping') {
        ws?.send(JSON.stringify({ type: 'pong' }));
        return;
      }

      if (msg.type === 'progress' && msg.job_id) {
        clientConnected = true;
        const existing = jobs.get(msg.job_id);
        jobs.set(msg.job_id, {
          id: msg.job_id,
          status: existing?.status || 'processing',
          progress: msg.data.progress || 0,
          speed: msg.data.speed || 0,
          fps: msg.data.fps || 0,
          currentTime: msg.data.time || 0,
          updatedAt: Date.now(),
        });
        jobs = jobs;
      }

      if (msg.type === 'status_change' && msg.job_id) {
        clientConnected = true;
        const existing = jobs.get(msg.job_id);
        if (existing) {
          existing.status = msg.data.status;
          existing.updatedAt = Date.now();
          jobs.set(msg.job_id, existing);
          jobs = jobs;
        } else {
          jobs.set(msg.job_id, {
            id: msg.job_id,
            status: msg.data.status,
            progress: 0,
            speed: 0,
            fps: 0,
            currentTime: 0,
            updatedAt: Date.now(),
          });
          jobs = jobs;
        }
      }
    };

    ws.onclose = () => {
      console.log('WebSocket disconnected');
      if (serverState === 'running') {
        setTimeout(connectWebSocket, 2000);
      }
    };

    ws.onerror = (e) => {
      console.error('WebSocket error:', e);
    };
  }

  function disconnectWebSocket() {
    if (ws) {
      ws.close();
      ws = null;
    }
  }

  function startHealthPolling() {
    healthInterval = window.setInterval(() => {
      pollHealth();
      cleanupOldJobs();
    }, 5000);
  }

  function cleanupOldJobs() {
    const MAX_JOBS = 50;
    const MAX_COMPLETED = 10;
    const COMPLETED_TTL = 2 * 60 * 1000; // 2 minutes
    
    const completedStatuses = ['ready', 'error', 'cancelled'];
    const now = Date.now();
    let cleaned = 0;
    
    // Remove completed jobs older than 2 minutes
    jobs.forEach((job, id) => {
      if (completedStatuses.includes(job.status) && (now - job.updatedAt) > COMPLETED_TTL) {
        jobs.delete(id);
        cleaned++;
      }
    });
    
    // If still too many completed jobs, keep only most recent
    const completed = [...jobs.entries()]
      .filter(([_, j]) => completedStatuses.includes(j.status))
      .sort((a, b) => b[1].updatedAt - a[1].updatedAt);
    
    if (completed.length > MAX_COMPLETED) {
      completed.slice(MAX_COMPLETED).forEach(([id]) => {
        jobs.delete(id);
        cleaned++;
      });
    }
    
    // Hard limit on total jobs
    if (jobs.size > MAX_JOBS) {
      const all = [...jobs.entries()].sort((a, b) => b[1].updatedAt - a[1].updatedAt);
      all.slice(MAX_JOBS).forEach(([id]) => {
        jobs.delete(id);
        cleaned++;
      });
    }
    
    if (cleaned > 0) {
      jobs = jobs;
    }
  }

  function stopHealthPolling() {
    if (healthInterval) {
      clearInterval(healthInterval);
      healthInterval = null;
    }
  }

  onMount(async () => {
    // Try to connect WebSocket - if server is already running, it will connect
    try {
      // Quick check if server might be running
      await invoke('check_server_health');
      serverState = 'running';
      connectWebSocket();
    } catch (e) {
      // Server not running, that's fine
    }
  });

  onDestroy(() => {
    stopHealthPolling();
    disconnectWebSocket();
  });
</script>

<main>
  <Header {serverState} {health} {capabilities} on:stop={stopServer} />
  
  {#if serverState === 'stopped'}
    <div class="center-content">
      <div class="start-panel">
        <img class="logo" src="/icons/Ghosthub192.png" alt="GhostStream" />
        <h1>GhostStream</h1>
        <p>Cross-platform transcoding server</p>
        <button class="btn-primary" on:click={startServer}>
          Start Server
        </button>
      </div>
    </div>
  {:else if serverState === 'starting' || (serverState === 'running' && !capabilities)}
    <SearchingOverlay message="Starting server..." />
  {:else if serverState === 'running' && capabilities}
    <div class="content">
      <StatusPanel {health} {capabilities} jobCount={activeJobs.length} {isGhostHub} {clientConnected} />
      <JobList jobs={jobList} />
    </div>
  {:else if serverState === 'stopping'}
    <SearchingOverlay message="Stopping server..." />
  {:else if serverState === 'error'}
    <div class="center-content">
      <div class="error-panel">
        <img class="logo" src="/icons/Ghosthub192.png" alt="GhostStream" />
        <h2>Server Error</h2>
        <p>Failed to start GhostStream</p>
        <button class="btn-primary" on:click={startServer}>
          Retry
        </button>
      </div>
    </div>
  {/if}
</main>

<style>
  main {
    flex: 1;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  .center-content {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .start-panel, .error-panel {
    text-align: center;
    padding: 3rem;
  }

  .logo {
    width: 80px;
    height: 80px;
    margin-bottom: 1rem;
  }

  h1 {
    font-size: 2rem;
    font-weight: 700;
    margin-bottom: 0.5rem;
  }

  h2 {
    font-size: 1.5rem;
    font-weight: 600;
    margin-bottom: 0.5rem;
  }

  p {
    color: var(--text-secondary);
    margin-bottom: 2rem;
  }

  .btn-primary {
    background: var(--accent);
    color: white;
    padding: 0.75rem 2rem;
    border-radius: var(--radius);
    font-size: 1rem;
    font-weight: 500;
  }

  .btn-primary:hover {
    background: var(--accent-hover);
  }

  .content {
    flex: 1;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    padding: 1rem;
    gap: 1rem;
  }
</style>

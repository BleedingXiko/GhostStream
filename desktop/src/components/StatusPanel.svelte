<script lang="ts">
  import type { Health, Capabilities } from '../types';

  export let health: Health | null;
  export let capabilities: Capabilities | null;
  export let jobCount: number;
  export let isGhostHub: boolean = false;
  export let clientConnected: boolean = false;
  export let displayAddress: string = 'localhost:8765';

  $: hwAccel = capabilities?.hw_accels?.find(h => h.available && h.type !== 'software');
  $: gpuName = hwAccel?.gpu_info?.name || 'Unknown GPU';
  $: encoders = hwAccel?.encoders?.join(', ') || 'Software only';
  
  $: statusText = clientConnected 
    ? (isGhostHub ? 'GhostHub Connected' : 'Client Connected')
    : `Listening on ${displayAddress}`;
  
  function formatUptime(seconds: number): string {
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    if (h > 0) return `${h}h ${m}m`;
    return `${m}m`;
  }
</script>

<div class="panel">
  <div class="client-status">
    <span class="dot" class:waiting={!clientConnected}></span>
    <span class="label">{statusText}</span>
  </div>
  
  <div class="stats">
    <div class="stat">
      <span class="value">{jobCount}</span>
      <span class="label">Active Jobs</span>
    </div>
    
    <div class="stat">
      <span class="value">{health ? formatUptime(health.uptime_seconds) : '--'}</span>
      <span class="label">Uptime</span>
    </div>
  </div>
</div>

<style>
  .panel {
    background: var(--bg-secondary);
    border-radius: var(--radius-lg);
    padding: 1rem;
    display: flex;
    align-items: center;
    justify-content: space-between;
  }

  .client-status {
    display: flex;
    align-items: center;
    gap: 0.5rem;
  }

  .dot {
    width: 8px;
    height: 8px;
    background: var(--success);
    border-radius: 50%;
    animation: pulse 2s ease-in-out infinite;
  }

  .dot.waiting {
    background: var(--warning);
  }

  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.5; }
  }

  .client-status .label {
    font-weight: 500;
  }

  .stats {
    display: flex;
    gap: 2rem;
  }

  .stat {
    text-align: right;
  }

  .stat .value {
    display: block;
    font-size: 1.25rem;
    font-weight: 600;
  }

  .stat .label {
    font-size: 0.75rem;
    color: var(--text-secondary);
  }
</style>

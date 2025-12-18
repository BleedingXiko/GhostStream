<script lang="ts">
  import { createEventDispatcher } from 'svelte';
  import type { ServerState, Health, Capabilities } from '../types';

  export let serverState: ServerState;
  export let health: Health | null;
  export let capabilities: Capabilities | null;

  const dispatch = createEventDispatcher();

  $: hwAccel = capabilities?.hw_accels?.find(h => h.available && h.type !== 'software');
  $: gpuName = hwAccel?.gpu_info?.name || null;
  $: encoderType = hwAccel?.type?.toUpperCase() || null;
  $: isOnline = serverState === 'running' && health?.status === 'healthy';

  function handleStop() {
    dispatch('stop');
  }
</script>

<header>
  <div class="left">
    <span class="title">GHOSTSTREAM</span>
    {#if gpuName}
      <div class="gpu-badge">
        <span class="gpu-name">{gpuName}</span>
        {#if encoderType}
          <span class="encoder" class:nvenc={encoderType === 'NVENC'} class:qsv={encoderType === 'QSV'} class:amf={encoderType === 'AMF'}>{encoderType}</span>
        {/if}
      </div>
    {/if}
  </div>
  
  <div class="right">
    {#if isOnline}
      <span class="status online">● Online</span>
    {:else if serverState === 'running'}
      <span class="status connecting">● Connecting</span>
    {:else}
      <span class="status offline">○ Offline</span>
    {/if}
    
    {#if serverState === 'running'}
      <button class="btn-stop" on:click={handleStop}>
        Stop
      </button>
    {/if}
  </div>
</header>

<style>
  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0.75rem 1rem;
    background: var(--bg-secondary);
    border-bottom: 1px solid var(--border);
    -webkit-app-region: drag;
  }

  .left, .right {
    display: flex;
    align-items: center;
    gap: 1rem;
  }

  .title {
    font-size: 0.875rem;
    font-weight: 700;
    letter-spacing: 0.05em;
  }

  .gpu-badge {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    font-size: 0.75rem;
    color: var(--text-secondary);
    background: var(--bg-tertiary);
    padding: 0.25rem 0.5rem;
    border-radius: var(--radius);
  }

  .gpu-name {
    max-width: 180px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .encoder {
    font-weight: 600;
    padding: 0.125rem 0.375rem;
    border-radius: var(--radius);
    background: var(--text-muted);
    color: white;
  }

  .encoder.nvenc {
    background: #76b900;
  }

  .encoder.qsv {
    background: #0071c5;
  }

  .encoder.amf {
    background: #ed1c24;
  }

  .status {
    font-size: 0.75rem;
    font-weight: 500;
  }

  .status.online {
    color: var(--success);
  }

  .status.connecting {
    color: var(--warning);
  }

  .status.offline {
    color: var(--text-muted);
  }

  .btn-stop {
    -webkit-app-region: no-drag;
    background: var(--bg-tertiary);
    color: var(--text-secondary);
    padding: 0.375rem 0.75rem;
    border-radius: var(--radius);
    font-size: 0.75rem;
    font-weight: 500;
  }

  .btn-stop:hover {
    background: var(--error);
    color: white;
  }
</style>

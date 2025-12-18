<script lang="ts">
  import type { Job } from '../types';

  export let job: Job;

  $: isActive = job.status === 'processing' || job.status === 'queued';
  $: statusColor = getStatusColor(job.status);
  $: statusLabel = getStatusLabel(job.status);

  function getStatusColor(status: string): string {
    switch (status) {
      case 'processing': return 'var(--accent)';
      case 'queued': return 'var(--warning)';
      case 'ready': return 'var(--success)';
      case 'error': return 'var(--error)';
      case 'cancelled': return 'var(--text-muted)';
      default: return 'var(--text-secondary)';
    }
  }

  function getStatusLabel(status: string): string {
    switch (status) {
      case 'processing': return 'Transcoding';
      case 'queued': return 'Queued';
      case 'ready': return 'Complete';
      case 'error': return 'Failed';
      case 'cancelled': return 'Cancelled';
      default: return status;
    }
  }

  function formatSpeed(speed: number): string {
    if (!speed || speed === 0) return '--';
    return `${speed.toFixed(1)}x`;
  }
</script>

<div class="card" class:active={isActive}>
  <div class="info">
    <div class="id">{job.id.slice(0, 8)}</div>
    <div class="meta">
      <span class="status" style="color: {statusColor}">{statusLabel}</span>
      {#if isActive && job.speed > 0}
        <span class="speed">Speed: {formatSpeed(job.speed)}</span>
      {/if}
    </div>
  </div>
  
  {#if isActive}
    <div class="progress-section">
      <div class="progress-bar">
        <div class="progress-fill" style="width: {job.progress}%"></div>
      </div>
      <span class="progress-text">{job.progress.toFixed(0)}%</span>
    </div>
  {/if}
</div>

<style>
  .card {
    background: var(--bg-secondary);
    border-radius: var(--radius);
    padding: 0.75rem 1rem;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
  }

  .card.active {
    border-left: 3px solid var(--accent);
  }

  .info {
    flex: 1;
    min-width: 0;
  }

  .id {
    font-family: monospace;
    font-size: 0.875rem;
    font-weight: 500;
    margin-bottom: 0.25rem;
  }

  .meta {
    display: flex;
    gap: 1rem;
    font-size: 0.75rem;
  }

  .status {
    font-weight: 500;
  }

  .speed {
    color: var(--text-secondary);
  }

  .progress-section {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    min-width: 150px;
  }

  .progress-bar {
    flex: 1;
    height: 6px;
    background: var(--bg-tertiary);
    border-radius: 3px;
    overflow: hidden;
  }

  .progress-fill {
    height: 100%;
    background: var(--accent);
    border-radius: 3px;
    transition: width 0.3s ease;
  }

  .progress-text {
    font-size: 0.875rem;
    font-weight: 600;
    min-width: 40px;
    text-align: right;
  }
</style>

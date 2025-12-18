<script lang="ts">
  import type { Job } from '../types';
  import JobCard from './JobCard.svelte';

  export let jobs: Job[];

  const MAX_ACTIVE_DISPLAY = 20;
  const MAX_COMPLETED_DISPLAY = 10;

  $: activeJobs = jobs.filter(j => j.status === 'processing' || j.status === 'queued');
  $: completedJobs = jobs.filter(j => j.status === 'ready' || j.status === 'error' || j.status === 'cancelled');
  $: displayedActive = activeJobs.slice(0, MAX_ACTIVE_DISPLAY);
  $: displayedCompleted = completedJobs.slice(0, MAX_COMPLETED_DISPLAY);
  $: hiddenActiveCount = Math.max(0, activeJobs.length - MAX_ACTIVE_DISPLAY);
  $: hiddenCompletedCount = Math.max(0, completedJobs.length - MAX_COMPLETED_DISPLAY);
</script>

<div class="job-list">
  {#if activeJobs.length > 0}
    <section>
      <h3>Active Jobs <span class="count">({activeJobs.length})</span></h3>
      <div class="jobs">
        {#each displayedActive as job (job.id)}
          <JobCard {job} />
        {/each}
        {#if hiddenActiveCount > 0}
          <div class="more">+{hiddenActiveCount} more in queue</div>
        {/if}
      </div>
    </section>
  {/if}

  {#if completedJobs.length > 0}
    <section>
      <h3>Recent <span class="count">({completedJobs.length})</span></h3>
      <div class="jobs">
        {#each displayedCompleted as job (job.id)}
          <JobCard {job} />
        {/each}
        {#if hiddenCompletedCount > 0}
          <div class="more">+{hiddenCompletedCount} older jobs</div>
        {/if}
      </div>
    </section>
  {/if}

  {#if jobs.length === 0}
    <div class="empty">
      <p>No transcoding jobs yet</p>
      <p class="hint">Jobs will appear here when a client starts transcoding</p>
    </div>
  {/if}
</div>

<style>
  .job-list {
    flex: 1;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    gap: 1.5rem;
  }

  section h3 {
    font-size: 0.75rem;
    font-weight: 600;
    color: var(--text-secondary);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    margin-bottom: 0.75rem;
  }

  section h3 .count {
    color: var(--text-muted);
    font-weight: 400;
  }

  .more {
    text-align: center;
    padding: 0.5rem;
    color: var(--text-muted);
    font-size: 0.875rem;
    background: var(--bg-tertiary);
    border-radius: var(--radius);
  }

  .jobs {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }

  .empty {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    color: var(--text-secondary);
  }

  .empty p {
    margin: 0;
  }

  .empty .hint {
    font-size: 0.875rem;
    color: var(--text-muted);
    margin-top: 0.25rem;
  }
</style>

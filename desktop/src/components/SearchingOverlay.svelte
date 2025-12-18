<script lang="ts">
  export let message: string;
  export let subtext: string = '';
</script>

<div class="overlay">
  <div class="content">
    <div class="spinner"></div>
    <p class="message">{message}</p>
    {#if subtext}
      <p class="subtext">{subtext}</p>
    {/if}
  </div>
</div>

<style>
  .overlay {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .content {
    text-align: center;
  }

  .spinner {
    width: 40px;
    height: 40px;
    margin: 0 auto 1.5rem;
    border: 3px solid var(--bg-tertiary);
    border-top-color: var(--accent);
    border-radius: 50%;
    animation: spin 1s linear infinite;
  }

  @keyframes spin {
    to { transform: rotate(360deg); }
  }

  .message {
    font-size: 1.125rem;
    font-weight: 500;
    margin-bottom: 0.5rem;
  }

  .subtext {
    font-size: 0.875rem;
    color: var(--text-secondary);
  }
</style>

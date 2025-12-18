import {
  TranscodeJob,
  TranscodeStatus,
  TranscodeOptions,
  ClientConfig,
  Capabilities,
  HealthStatus,
  ProgressEvent
} from './types';

/**
 * GhostStream JavaScript Client
 * 
 * @example
 * ```typescript
 * const client = new GhostStreamClient('192.168.4.2:8765');
 * 
 * // Health check
 * if (await client.healthCheck()) {
 *   console.log('Server is online!');
 * }
 * 
 * // Start transcode
 * const job = await client.transcode({
 *   source: 'http://example.com/video.mp4',
 *   resolution: '720p'
 * });
 * 
 * console.log(`Stream URL: ${job.streamUrl}`);
 * 
 * // Wait for ready
 * const result = await client.waitForReady(job.jobId);
 * if (result.status === TranscodeStatus.READY) {
 *   console.log('Ready to play!');
 * }
 * ```
 */
export class GhostStreamClient {
  private baseUrl: string;
  private wsUrl: string;
  private config: ClientConfig;

  /**
   * Create a new GhostStream client
   * @param server - Server address (e.g., '192.168.4.2:8765')
   * @param config - Optional client configuration
   */
  constructor(server: string, config: ClientConfig = {}) {
    // Add http:// if no protocol specified
    if (!server.startsWith('http://') && !server.startsWith('https://')) {
      server = `http://${server}`;
    }
    this.baseUrl = server.replace(/\/$/, '');
    this.wsUrl = this.baseUrl.replace('http://', 'ws://').replace('https://', 'wss://');
    
    this.config = {
      timeout: config.timeout ?? 30000,
      retries: config.retries ?? 3,
      retryDelay: config.retryDelay ?? 1000
    };
  }

  /**
   * Make an HTTP request with retries
   */
  private async request<T>(
    method: string,
    path: string,
    body?: unknown
  ): Promise<T | null> {
    const url = `${this.baseUrl}${path}`;
    
    for (let attempt = 0; attempt < (this.config.retries ?? 3); attempt++) {
      try {
        const controller = new AbortController();
        const timeoutId = setTimeout(
          () => controller.abort(),
          this.config.timeout
        );

        const response = await fetch(url, {
          method,
          headers: {
            'Content-Type': 'application/json'
          },
          body: body ? JSON.stringify(body) : undefined,
          signal: controller.signal
        });

        clearTimeout(timeoutId);

        if (!response.ok) {
          throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }

        return await response.json() as T;
      } catch (error) {
        if (attempt === (this.config.retries ?? 3) - 1) {
          console.error(`Request failed: ${error}`);
          return null;
        }
        await this.sleep(this.config.retryDelay ?? 1000);
      }
    }
    return null;
  }

  private sleep(ms: number): Promise<void> {
    return new Promise(resolve => setTimeout(resolve, ms));
  }

  /**
   * Convert snake_case API response to camelCase
   */
  private toTranscodeJob(data: Record<string, unknown>): TranscodeJob {
    return {
      jobId: data.job_id as string,
      status: data.status as TranscodeStatus,
      progress: (data.progress as number) ?? 0,
      streamUrl: data.stream_url as string | undefined,
      downloadUrl: data.download_url as string | undefined,
      duration: data.duration as number | undefined,
      currentTime: data.current_time as number | undefined,
      etaSeconds: data.eta_seconds as number | undefined,
      hwAccelUsed: data.hw_accel_used as string | undefined,
      errorMessage: data.error_message as string | undefined,
      createdAt: data.created_at as string | undefined,
      startedAt: data.started_at as string | undefined
    };
  }

  // ==================== Health & Capabilities ====================

  /**
   * Check if the server is healthy
   */
  async healthCheck(): Promise<boolean> {
    const result = await this.request<HealthStatus>('GET', '/api/health');
    return result?.status === 'healthy';
  }

  /**
   * Get server capabilities (codecs, hardware, etc.)
   */
  async getCapabilities(): Promise<Capabilities | null> {
    return await this.request<Capabilities>('GET', '/api/capabilities');
  }

  // ==================== Transcoding ====================

  /**
   * Start a transcoding job
   */
  async transcode(options: TranscodeOptions): Promise<TranscodeJob> {
    const body = {
      source: options.source,
      mode: options.mode ?? 'stream',
      start_time: options.startTime ?? 0,
      output: {
        format: options.format ?? 'hls',
        video_codec: options.videoCodec ?? 'h264',
        audio_codec: options.audioCodec ?? 'aac',
        resolution: options.resolution ?? '1080p',
        bitrate: options.bitrate ?? 'auto',
        hw_accel: options.hwAccel ?? 'auto',
        tone_map: options.toneMap ?? true,
        two_pass: options.twoPass ?? false
      }
    };

    const result = await this.request<Record<string, unknown>>(
      'POST',
      '/api/transcode/start',
      body
    );

    if (!result) {
      return {
        jobId: '',
        status: TranscodeStatus.ERROR,
        progress: 0,
        errorMessage: 'Failed to start transcode'
      };
    }

    return this.toTranscodeJob(result);
  }

  /**
   * Get the status of a job
   */
  async getJobStatus(jobId: string): Promise<TranscodeJob | null> {
    const result = await this.request<Record<string, unknown>>(
      'GET',
      `/api/transcode/${jobId}/status`
    );
    return result ? this.toTranscodeJob(result) : null;
  }

  /**
   * Wait for a job to be ready
   */
  async waitForReady(
    jobId: string,
    timeout: number = 300000,
    pollInterval: number = 1000
  ): Promise<TranscodeJob | null> {
    const startTime = Date.now();

    while (Date.now() - startTime < timeout) {
      const job = await this.getJobStatus(jobId);
      
      if (!job) {
        await this.sleep(pollInterval);
        continue;
      }

      if (
        job.status === TranscodeStatus.READY ||
        job.status === TranscodeStatus.ERROR ||
        job.status === TranscodeStatus.CANCELLED
      ) {
        return job;
      }

      // For streaming, return early if we have a URL and it's processing
      if (
        job.status === TranscodeStatus.PROCESSING &&
        job.streamUrl &&
        job.progress > 5
      ) {
        return job;
      }

      await this.sleep(pollInterval);
    }

    return null;
  }

  /**
   * Cancel a running job
   */
  async cancelJob(jobId: string): Promise<boolean> {
    const result = await this.request<{ status: string }>(
      'POST',
      `/api/transcode/${jobId}/cancel`
    );
    return result?.status === 'cancelled';
  }

  /**
   * Delete a job and cleanup temp files
   */
  async deleteJob(jobId: string): Promise<boolean> {
    const result = await this.request<{ status: string }>(
      'DELETE',
      `/api/transcode/${jobId}`
    );
    return result !== null;
  }

  // ==================== WebSocket ====================

  /**
   * Subscribe to real-time progress updates via WebSocket
   * 
   * @example
   * ```typescript
   * for await (const event of client.subscribeProgress([job.jobId])) {
   *   if (event.type === 'progress') {
   *     console.log(`Progress: ${event.data?.progress}%`);
   *   }
   * }
   * ```
   */
  async *subscribeProgress(
    jobIds: string[]
  ): AsyncGenerator<ProgressEvent, void, unknown> {
    const ws = new WebSocket(`${this.wsUrl}/ws/progress`);
    
    const messageQueue: ProgressEvent[] = [];
    let resolveNext: ((value: ProgressEvent) => void) | null = null;
    let closed = false;

    ws.onopen = () => {
      ws.send(JSON.stringify({
        type: 'subscribe',
        job_ids: jobIds
      }));
    };

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data) as ProgressEvent;
      
      // Handle pings
      if (data.type === 'ping') {
        ws.send(JSON.stringify({ type: 'pong' }));
        return;
      }

      if (resolveNext) {
        resolveNext(data);
        resolveNext = null;
      } else {
        messageQueue.push(data);
      }
    };

    ws.onclose = () => {
      closed = true;
      if (resolveNext) {
        resolveNext({ type: 'status_change', data: { status: 'disconnected' } });
      }
    };

    ws.onerror = () => {
      closed = true;
    };

    try {
      while (!closed) {
        if (messageQueue.length > 0) {
          const event = messageQueue.shift()!;
          yield event;
          
          // Check if job is done
          if (
            event.type === 'status_change' &&
            ['ready', 'error', 'cancelled'].includes(event.data?.status ?? '')
          ) {
            break;
          }
        } else {
          const event = await new Promise<ProgressEvent>(resolve => {
            resolveNext = resolve;
          });
          yield event;
          
          if (
            event.type === 'status_change' &&
            ['ready', 'error', 'cancelled'].includes(event.data?.status ?? '')
          ) {
            break;
          }
        }
      }
    } finally {
      ws.close();
    }
  }
}

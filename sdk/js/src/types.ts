/**
 * Transcode job status
 */
export enum TranscodeStatus {
  QUEUED = 'queued',
  PROCESSING = 'processing',
  READY = 'ready',
  ERROR = 'error',
  CANCELLED = 'cancelled'
}

/**
 * Transcode job information
 */
export interface TranscodeJob {
  jobId: string;
  status: TranscodeStatus;
  progress: number;
  streamUrl?: string;
  downloadUrl?: string;
  duration?: number;
  currentTime?: number;
  etaSeconds?: number;
  hwAccelUsed?: string;
  errorMessage?: string;
  createdAt?: string;
  startedAt?: string;
}

/**
 * Options for starting a transcode job
 */
export interface TranscodeOptions {
  /** Source URL (required) */
  source: string;
  /** Transcode mode: 'stream', 'abr', or 'batch' */
  mode?: 'stream' | 'abr' | 'batch';
  /** Output format: 'hls', 'mp4', 'webm', 'mkv' */
  format?: string;
  /** Resolution: '4k', '1080p', '720p', '480p', 'original' */
  resolution?: string;
  /** Video codec: 'h264', 'h265', 'vp9', 'av1', 'copy' */
  videoCodec?: string;
  /** Audio codec: 'aac', 'opus', 'mp3', 'copy' */
  audioCodec?: string;
  /** Bitrate: 'auto' or specific like '8M' */
  bitrate?: string;
  /** Hardware acceleration: 'auto', 'nvenc', 'qsv', 'software' */
  hwAccel?: string;
  /** Start position in seconds (for seeking) */
  startTime?: number;
  /** HDR to SDR tone mapping */
  toneMap?: boolean;
  /** Two-pass encoding (batch mode only) */
  twoPass?: boolean;
}

/**
 * Client configuration
 */
export interface ClientConfig {
  /** Request timeout in milliseconds */
  timeout?: number;
  /** Number of retry attempts */
  retries?: number;
  /** Retry delay in milliseconds */
  retryDelay?: number;
}

/**
 * Server capabilities
 */
export interface Capabilities {
  hwAccels: Array<{
    type: string;
    available: boolean;
    encoders?: string[];
    gpuInfo?: {
      name: string;
      memoryMb: number;
    };
  }>;
  videoCodecs: string[];
  audioCodecs: string[];
  formats: string[];
  maxConcurrentJobs: number;
  ffmpegVersion: string;
  platform: string;
}

/**
 * Health check response
 */
export interface HealthStatus {
  status: string;
  version: string;
  uptimeSeconds: number;
  currentJobs: number;
  queuedJobs: number;
}

/**
 * WebSocket progress event
 */
export interface ProgressEvent {
  type: 'progress' | 'status_change' | 'ping';
  jobId?: string;
  data?: {
    progress?: number;
    frame?: number;
    fps?: number;
    time?: number;
    speed?: number;
    status?: string;
  };
}

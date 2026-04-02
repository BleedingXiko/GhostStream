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
 * Subtitle track information
 */
export interface SubtitleTrack {
  /** URL to the WebVTT subtitle file */
  url: string;
  /** Display label for the track (e.g. 'English') */
  label?: string;
  /** ISO 639-1 language code (e.g. 'en') */
  language?: string;
  /** Whether this track is the default */
  default?: boolean;
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
  controlToken?: string;
  duration?: number;
  currentTime?: number;
  etaSeconds?: number;
  hwAccelUsed?: string;
  errorMessage?: string;
  createdAt?: string;
  startedAt?: string;
  completedAt?: string;
  startTime?: number;
  isShared?: boolean;
  viewerCount?: number;
  variants?: Array<Record<string, unknown>>;
  mediaInfo?: Record<string, unknown>;
  subtitles?: SubtitleTrack[];
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
  /** Optional subtitle tracks */
  subtitles?: SubtitleTrack[];
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
  /** Client name sent to the server on WebSocket connect (e.g. 'GhostHub') */
  clientName?: string;
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
  type: 'progress' | 'status_change' | 'ping' | 'error';
  jobId?: string;
  data?: {
    progress?: number;
    frame?: number;
    fps?: number;
    time?: number;
    speed?: number;
    status?: string;
    error?: string;
  };
}

export type ServerState = 'stopped' | 'starting' | 'running' | 'stopping' | 'error';

export interface Job {
  id: string;
  status: string;
  progress: number;
  speed: number;
  fps: number;
  currentTime: number;
  updatedAt: number;
}

export interface Health {
  status: string;
  version: string;
  uptime_seconds: number;
  current_jobs: number;
  queued_jobs: number;
}

export interface HwAccel {
  type: string;
  available: boolean;
  encoders: string[];
  gpu_info?: {
    name: string;
    memory_mb: number;
  };
}

export interface Capabilities {
  hw_accels: HwAccel[];
  video_codecs: string[];
  audio_codecs: string[];
  formats: string[];
  max_concurrent_jobs: number;
  ffmpeg_version: string;
  platform: string;
}

# GhostStream JavaScript SDK

Official JavaScript/TypeScript SDK for [GhostStream](https://github.com/BleedingXiko/GhostStream) transcoding service.

## Installation

```bash
npm install ghoststream-sdk
```

## Quick Start

```typescript
import { GhostStreamClient, TranscodeStatus } from 'ghoststream-sdk';

const client = new GhostStreamClient('192.168.4.2:8765');

// Start transcoding
const job = await client.transcode({
  source: 'http://example.com/video.mp4',
  resolution: '720p'
});

console.log(`Stream URL: ${job.streamUrl}`);

// Wait for ready
const result = await client.waitForReady(job.jobId);
if (result?.status === TranscodeStatus.READY) {
  console.log('Ready to play!');
}

// Cleanup
await client.deleteJob(job.jobId);
```

## API Reference

### Constructor

```typescript
const client = new GhostStreamClient(server: string, config?: ClientConfig);
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `server` | `string` | Server address (e.g., `'192.168.4.2:8765'`) |
| `config.timeout` | `number` | Request timeout in ms (default: 30000) |
| `config.retries` | `number` | Retry attempts (default: 3) |

### Methods

#### `healthCheck(): Promise<boolean>`

Check if the server is online.

```typescript
if (await client.healthCheck()) {
  console.log('Server is healthy!');
}
```

#### `getCapabilities(): Promise<Capabilities | null>`

Get server capabilities (codecs, hardware acceleration, etc.).

```typescript
const caps = await client.getCapabilities();
console.log(`Video codecs: ${caps?.videoCodecs}`);
console.log(`GPU available: ${caps?.hwAccels.some(h => h.available)}`);
```

#### `transcode(options: TranscodeOptions): Promise<TranscodeJob>`

Start a transcoding job.

```typescript
const job = await client.transcode({
  source: 'http://example.com/video.mp4',
  mode: 'stream',        // 'stream', 'abr', or 'batch'
  resolution: '1080p',   // '4k', '1080p', '720p', '480p', 'original'
  videoCodec: 'h264',    // 'h264', 'h265', 'vp9', 'av1'
  audioCodec: 'aac',     // 'aac', 'opus', 'mp3'
  hwAccel: 'auto',       // 'auto', 'nvenc', 'qsv', 'software'
  startTime: 0,          // Seek position in seconds
  toneMap: true          // HDR to SDR conversion
});
```

#### `getJobStatus(jobId: string): Promise<TranscodeJob | null>`

Get the current status of a job.

```typescript
const status = await client.getJobStatus(job.jobId);
console.log(`Progress: ${status?.progress}%`);
```

#### `waitForReady(jobId: string, timeout?: number): Promise<TranscodeJob | null>`

Wait for a job to be ready (or error/cancel).

```typescript
const result = await client.waitForReady(job.jobId, 60000); // 60s timeout
if (result?.status === TranscodeStatus.READY) {
  // Play the stream
}
```

#### `cancelJob(jobId: string): Promise<boolean>`

Cancel a running job.

```typescript
await client.cancelJob(job.jobId);
```

#### `deleteJob(jobId: string): Promise<boolean>`

Delete a job and cleanup temp files.

```typescript
await client.deleteJob(job.jobId);
```

#### `subscribeProgress(jobIds: string[]): AsyncGenerator<ProgressEvent>`

Subscribe to real-time progress updates via WebSocket.

```typescript
for await (const event of client.subscribeProgress([job.jobId])) {
  if (event.type === 'progress') {
    console.log(`Progress: ${event.data?.progress}%`);
  } else if (event.type === 'status_change') {
    console.log(`Status: ${event.data?.status}`);
    if (event.data?.status === 'ready') break;
  }
}
```

## Types

### TranscodeStatus

```typescript
enum TranscodeStatus {
  QUEUED = 'queued',
  PROCESSING = 'processing',
  READY = 'ready',
  ERROR = 'error',
  CANCELLED = 'cancelled'
}
```

### TranscodeJob

```typescript
interface TranscodeJob {
  jobId: string;
  status: TranscodeStatus;
  progress: number;
  streamUrl?: string;
  downloadUrl?: string;
  duration?: number;
  hwAccelUsed?: string;
  errorMessage?: string;
}
```

## Examples

### Basic Streaming

```typescript
const client = new GhostStreamClient('localhost:8765');

const job = await client.transcode({
  source: 'http://example.com/movie.mkv',
  resolution: '720p'
});

// Use job.streamUrl in your video player
console.log(job.streamUrl);
// -> http://localhost:8765/stream/xxx/master.m3u8
```

### Adaptive Bitrate (ABR)

```typescript
const job = await client.transcode({
  source: 'http://example.com/4k-movie.mkv',
  mode: 'abr'  // Creates 1080p, 720p, 480p variants
});

// Master playlist contains all quality variants
console.log(job.streamUrl);
```

### With Progress Updates

```typescript
const job = await client.transcode({
  source: 'http://example.com/video.mp4'
});

for await (const event of client.subscribeProgress([job.jobId])) {
  if (event.type === 'progress') {
    const progress = event.data?.progress ?? 0;
    console.log(`Transcoding: ${progress.toFixed(1)}%`);
  }
  
  if (event.type === 'status_change' && event.data?.status === 'ready') {
    console.log('Done!');
    break;
  }
}
```

### React Example

```tsx
import { useState, useEffect } from 'react';
import { GhostStreamClient, TranscodeJob, TranscodeStatus } from 'ghoststream-sdk';

const client = new GhostStreamClient('localhost:8765');

function VideoPlayer({ sourceUrl }: { sourceUrl: string }) {
  const [job, setJob] = useState<TranscodeJob | null>(null);
  const [progress, setProgress] = useState(0);

  useEffect(() => {
    let cancelled = false;

    async function startTranscode() {
      const newJob = await client.transcode({
        source: sourceUrl,
        resolution: '720p'
      });
      
      if (cancelled) return;
      setJob(newJob);

      // Watch progress
      for await (const event of client.subscribeProgress([newJob.jobId])) {
        if (cancelled) break;
        
        if (event.type === 'progress') {
          setProgress(event.data?.progress ?? 0);
        }
        if (event.data?.status === 'ready') break;
      }
    }

    startTranscode();

    return () => {
      cancelled = true;
      if (job) client.deleteJob(job.jobId);
    };
  }, [sourceUrl]);

  if (!job) return <div>Starting transcode...</div>;
  if (job.status === TranscodeStatus.ERROR) return <div>Error: {job.errorMessage}</div>;

  return (
    <div>
      {progress < 100 && <div>Transcoding: {progress.toFixed(0)}%</div>}
      {job.streamUrl && (
        <video src={job.streamUrl} controls autoPlay />
      )}
    </div>
  );
}
```

## Browser Support

Works in all modern browsers that support:
- `fetch` API
- `WebSocket` API
- `async/await`

For Node.js, you may need to polyfill `fetch` and `WebSocket`:

```typescript
import fetch from 'node-fetch';
import WebSocket from 'ws';

globalThis.fetch = fetch;
globalThis.WebSocket = WebSocket;
```

## License

MIT

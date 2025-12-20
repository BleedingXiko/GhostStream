# GhostStream API Documentation

## Overview

GhostStream runs on your PC and provides transcoding services over the network. GhostHub (or any client) can discover it automatically or connect manually.

**Base URL:** `http://<your-pc-ip>:8765`

---

## Quick Start

### 1. Start GhostStream on your PC

```bash
# Check your hardware first
python -m ghoststream --detect-hw

# Start the server
python -m ghoststream
```

### 2. Install SDK

```bash
pip install ghoststream
```

### 3. Connect from GhostHub (Python)

```python
from ghoststream import GhostStreamClient, TranscodeStatus

# Option A: Auto-discover via mDNS
client = GhostStreamClient()
client.start_discovery()

# Option B: Manual connection (recommended)
client = GhostStreamClient(manual_server="192.168.4.2:8765")

# Sync usage (Flask/GhostHub compatible)
job = client.transcode_sync(source="http://pi:5000/video.mkv", resolution="720p")
print(f"Stream: {job.stream_url}")

# Async usage
async with GhostStreamClient(manual_server="192.168.4.2:8765") as client:
    job = await client.transcode(source="http://pi:5000/video.mkv")
    print(f"Stream: {job.stream_url}")
```

---

## REST API Endpoints

### Health Check

Check if GhostStream is running.

```
GET /api/health
```

**Response:**
```json
{
  "status": "healthy",
  "version": "1.0.0",
  "uptime_seconds": 3600.5,
  "current_jobs": 1,
  "queued_jobs": 0
}
```

**Example:**
```bash
curl http://192.168.4.2:8765/api/health
```

---

### Get Capabilities

See what codecs and hardware acceleration are available.

```
GET /api/capabilities
```

**Response:**
```json
{
  "hw_accels": [
    {
      "type": "nvenc",
      "available": true,
      "encoders": ["h264_nvenc", "hevc_nvenc", "av1_nvenc"],
      "gpu_info": {
        "name": "NVIDIA GeForce GTX 1660 Ti",
        "memory_mb": 6144
      }
    },
    {
      "type": "software",
      "available": true,
      "encoders": ["libx264", "libx265", "libvpx-vp9"]
    }
  ],
  "video_codecs": ["h264", "h265", "vp9", "av1"],
  "audio_codecs": ["aac", "opus", "mp3", "copy"],
  "formats": ["hls", "mp4", "webm", "mkv"],
  "max_concurrent_jobs": 2,
  "ffmpeg_version": "6.0",
  "platform": "Windows 10"
}
```

**Example:**
```bash
curl http://192.168.4.2:8765/api/capabilities
```

---

### Get Statistics

View transcoding statistics.

```
GET /api/stats
```

**Response:**
```json
{
  "total_jobs_processed": 42,
  "successful_jobs": 40,
  "failed_jobs": 1,
  "cancelled_jobs": 1,
  "current_queue_length": 0,
  "active_jobs": 1,
  "average_transcode_speed": 2.5,
  "uptime_seconds": 86400,
  "hw_accel_usage": {
    "nvenc": 35,
    "software": 5
  }
}
```

---

### Start Transcoding

Start a new transcoding job.

```
POST /api/transcode/start
Content-Type: application/json
```

**Request Body:**
```json
{
  "source": "http://192.168.4.1:5000/media/movie.mkv",
  "mode": "stream",
  "output": {
    "format": "hls",
    "video_codec": "h264",
    "audio_codec": "aac",
    "resolution": "1080p",
    "bitrate": "auto",
    "hw_accel": "auto",
    "tone_map": true,
    "two_pass": false,
    "max_audio_channels": 2
  },
  "start_time": 0,
  "subtitles": [
    {
      "url": "http://192.168.4.1:5000/subtitles/english.vtt",
      "label": "English",
      "language": "en",
      "default": true
    },
    {
      "url": "http://192.168.4.1:5000/subtitles/spanish.vtt",
      "label": "Español",
      "language": "es",
      "default": false
    }
  ],
  "callback_url": null
}
```

**Parameters:**

| Field | Type | Description |
|-------|------|-------------|
| `source` | string | **Required.** URL where GhostStream can fetch the video |
| `mode` | string | `"stream"` (HLS), `"abr"` (adaptive bitrate), or `"batch"` (file) |
| `output.format` | string | `hls`, `mp4`, `webm`, `mkv` |
| `output.video_codec` | string | `h264`, `h265`, `vp9`, `av1`, `copy` |
| `output.audio_codec` | string | `aac`, `opus`, `mp3`, `copy` |
| `output.resolution` | string | `4k`, `1080p`, `720p`, `480p`, `original` |
| `output.bitrate` | string | `auto` or specific like `"8M"` |
| `output.hw_accel` | string | `auto`, `nvenc`, `qsv`, `vaapi`, `software` |
| `output.tone_map` | bool | Convert HDR to SDR automatically (default: true) |
| `output.two_pass` | bool | Two-pass encoding for batch mode (default: false) |
| `output.max_audio_channels` | int | Max audio channels, 2=stereo, 6=5.1 (default: 2) |
| `start_time` | number | Start position in seconds (for seeking) |
| `subtitles` | array | Optional subtitle tracks to mux into HLS stream (WebVTT format) |
| `subtitles[].url` | string | URL to fetch WebVTT subtitle file |
| `subtitles[].label` | string | Display name for subtitle track (e.g., "English") |
| `subtitles[].language` | string | ISO 639-1 language code (e.g., "en", "es") |
| `subtitles[].default` | bool | Whether this subtitle track is selected by default |
| `callback_url` | string | URL to POST when job completes (optional) |

**Modes:**

| Mode | Description |
|------|-------------|
| `stream` | Single-quality HLS streaming (fastest startup) |
| `abr` | Adaptive bitrate - generates multiple quality variants (1080p, 720p, 480p, etc.) |
| `batch` | File-to-file transcoding with optional two-pass |

**Response:**
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "processing",
  "progress": 0,
  "stream_url": "http://192.168.4.2:8765/stream/550e8400.../master.m3u8",
  "duration": 7200.5,
  "hw_accel_used": "nvenc"
}
```

**Example - Standard Stream:**
```bash
curl -X POST http://192.168.4.2:8765/api/transcode/start \
  -H "Content-Type: application/json" \
  -d '{
    "source": "http://192.168.4.1:5000/media/video.mkv",
    "mode": "stream",
    "output": {
      "video_codec": "h264",
      "resolution": "1080p"
    }
  }'
```

**Example - Adaptive Bitrate (ABR):**
```bash
curl -X POST http://192.168.4.2:8765/api/transcode/start \
  -H "Content-Type: application/json" \
  -d '{
    "source": "http://192.168.4.1:5000/media/4k-hdr-movie.mkv",
    "mode": "abr",
    "output": {
      "video_codec": "h264",
      "hw_accel": "auto"
    }
  }'
```

ABR automatically creates quality variants based on source resolution (won't upscale).

**Example - With Subtitles:**
```bash
curl -X POST http://192.168.4.2:8765/api/transcode/start \
  -H "Content-Type: application/json" \
  -d '{
    "source": "http://192.168.4.1:5000/media/movie.mkv",
    "mode": "stream",
    "output": {
      "video_codec": "h264",
      "resolution": "1080p"
    },
    "subtitles": [
      {
        "url": "http://192.168.4.1:5000/subtitles/english.vtt",
        "label": "English",
        "language": "en",
        "default": true
      },
      {
        "url": "http://192.168.4.1:5000/subtitles/spanish.vtt",
        "label": "Español",
        "language": "es",
        "default": false
      }
    ]
  }'
```

Subtitles are muxed directly into the HLS stream using WebVTT format and appear as native subtitle tracks in the HLS master playlist (`EXT-X-MEDIA:TYPE=SUBTITLES`). Players like HLS.js, VLC, and Safari will display them as selectable subtitle tracks.

---

### Get Job Status

Check the status of a transcoding job.

```
GET /api/transcode/{job_id}/status
```

**Response:**
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "processing",
  "progress": 45.2,
  "current_time": 542.5,
  "duration": 1200.0,
  "stream_url": "http://192.168.4.2:8765/stream/550e8400.../master.m3u8",
  "eta_seconds": 120,
  "hw_accel_used": "nvenc",
  "created_at": "2024-01-15T10:30:00Z",
  "started_at": "2024-01-15T10:30:01Z"
}
```

**Status Values:**
| Status | Description |
|--------|-------------|
| `queued` | Waiting to start |
| `processing` | Currently transcoding |
| `ready` | Complete, ready for download/streaming |
| `error` | Failed (check `error_message`) |
| `cancelled` | Cancelled by user |

---

### Cancel Job

Cancel a running or queued job.

```
POST /api/transcode/{job_id}/cancel
```

**Response:**
```json
{
  "status": "cancelled",
  "job_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

---

### Stream HLS Content

Access the transcoded HLS stream.

```
GET /stream/{job_id}/master.m3u8
GET /stream/{job_id}/segment_00001.ts
```

Use the `stream_url` from the transcode response directly in any HLS-compatible video player.

**VOD-Style Streaming:** GhostStream serves HLS playlists with proper VOD markers (`#EXT-X-PLAYLIST-TYPE:VOD` and `#EXT-X-ENDLIST`), enabling full seeking from the start—even while transcoding is still in progress. Players like HLS.js will treat the stream as seekable VOD content rather than live.

---

## WebSocket API

Real-time progress updates with **production-grade features**:
- Job subscription filtering (only receive updates for jobs you care about)
- Automatic heartbeat/keepalive
- Backpressure handling (server won't overwhelm slow clients)
- Connection limits (max 1000 concurrent connections)

```
WS /ws/progress
```

### Basic Connection

```javascript
const ws = new WebSocket('ws://192.168.4.2:8765/ws/progress');

ws.onmessage = (event) => {
  const msg = JSON.parse(event.data);
  
  if (msg.type === 'progress') {
    console.log(`Job ${msg.job_id}: ${msg.data.progress}%`);
  }
  
  if (msg.type === 'status_change') {
    console.log(`Job ${msg.job_id} is now ${msg.data.status}`);
  }
  
  // Respond to server pings to stay connected
  if (msg.type === 'ping') {
    ws.send(JSON.stringify({ type: 'pong' }));
  }
};
```

### Job Subscriptions (Recommended)

By default, you receive updates for ALL jobs. For better efficiency, subscribe only to jobs you care about:

```javascript
// Subscribe to specific jobs only
ws.send(JSON.stringify({
  type: 'subscribe',
  job_ids: ['job-123', 'job-456']
}));

// Unsubscribe when done watching
ws.send(JSON.stringify({
  type: 'unsubscribe', 
  job_ids: ['job-123']
}));

// Switch back to receiving all updates
ws.send(JSON.stringify({
  type: 'subscribe_all'
}));
```

### Client → Server Messages

| Type | Payload | Description |
|------|---------|-------------|
| `ping` | `{}` | Keepalive ping |
| `pong` | `{}` | Response to server ping |
| `subscribe` | `{job_ids: [...]}` | Subscribe to specific jobs |
| `unsubscribe` | `{job_ids: [...]}` | Unsubscribe from jobs |
| `subscribe_all` | `{}` | Receive all job updates (default) |

### Server → Client Messages

**Progress Update:**
```json
{
  "type": "progress",
  "job_id": "550e8400...",
  "data": {
    "progress": 45.2,
    "frame": 1200,
    "fps": 120.5,
    "time": 542.5,
    "speed": 2.5
  }
}
```

**Status Change:**
```json
{
  "type": "status_change",
  "job_id": "550e8400...",
  "data": {
    "status": "ready"
  }
}
```

**Ping (keepalive):**
```json
{
  "type": "ping",
  "ts": 1702656000.123
}
```

### WebSocket Progress (SDK)

The SDK has built-in WebSocket support:

```python
import asyncio
from ghoststream import GhostStreamClient, TranscodeStatus

async def transcode_with_progress():
    client = GhostStreamClient(manual_server="192.168.4.2:8765")
    
    # Start a job
    job = client.transcode_sync(source="http://pi:5000/video.mkv")
    
    # Watch progress via WebSocket
    async for event in client.subscribe_progress([job.job_id]):
        if event["type"] == "progress":
            print(f"Progress: {event['data']['progress']:.1f}%")
        elif event["type"] == "status_change":
            status = event["data"]["status"]
            print(f"Status: {status}")
            if status in ("ready", "error", "cancelled"):
                break
    
    # Cleanup
    client.delete_job_sync(job.job_id)

asyncio.run(transcode_with_progress())
```

### WebSocket (Raw)

For non-SDK usage:

```python
import asyncio
import websockets
import json

async def watch_job(server_url: str, job_id: str):
    """Watch a specific job's progress via WebSocket."""
    uri = f"ws://{server_url}/ws/progress"
    
    async with websockets.connect(uri) as ws:
        # Subscribe to only this job
        await ws.send(json.dumps({
            "type": "subscribe",
            "job_ids": [job_id]
        }))
        
        async for message in ws:
            data = json.loads(message)
            
            if data["type"] == "ping":
                await ws.send(json.dumps({"type": "pong"}))
                
            elif data["type"] == "progress":
                print(f"Progress: {data['data']['progress']:.1f}%")
                
            elif data["type"] == "status_change":
                status = data["data"]["status"]
                print(f"Status: {status}")
                if status in ("ready", "error", "cancelled"):
                    break  # Job finished

asyncio.run(watch_job("192.168.4.2:8765", "your-job-id"))
```

---

## Python SDK

The official SDK for integrating with GhostStream.

### Installation

```bash
pip install ghoststream
```

### Sync Usage (Flask/GhostHub)

```python
from ghoststream import GhostStreamClient, TranscodeStatus

# Create client
client = GhostStreamClient(manual_server="192.168.4.2:8765")

# Check health
if client.health_check_sync():
    print("GhostStream is online!")

# Get capabilities
caps = client.get_capabilities_sync()
print(f"Available codecs: {caps['video_codecs']}")

# Start transcoding
job = client.transcode_sync(
    source="http://192.168.4.1:5000/media/movie.mkv",
    mode="stream",
    resolution="1080p"
)

if job.status == TranscodeStatus.ERROR:
    print(f"Error: {job.error_message}")
else:
    print(f"Stream URL: {job.stream_url}")

# Wait for ready
result = client.wait_for_ready_sync(job.job_id, timeout=60)
if result and result.status == TranscodeStatus.READY:
    print(f"Play: {result.stream_url}")

# Cleanup when done
client.delete_job_sync(job.job_id)
```

### Async Usage

```python
import asyncio
from ghoststream import GhostStreamClient, TranscodeStatus

async def main():
    async with GhostStreamClient(manual_server="192.168.4.2:8765") as client:
        # Check health
        if await client.health_check():
            print("GhostStream is online!")
        
        # Start transcoding
        job = await client.transcode(
            source="http://192.168.4.1:5000/media/movie.mkv",
            resolution="1080p"
        )
        
        print(f"Stream URL: {job.stream_url}")
        
        # Wait for ready
        result = await client.wait_for_ready(job.job_id)
        
        if result.status == TranscodeStatus.READY:
            print(f"Play: {result.stream_url}")
        
        # Cleanup
        await client.delete_job(job.job_id)

asyncio.run(main())
```

### Auto-Discovery (mDNS)

```python
from ghoststream import GhostStreamClient

client = GhostStreamClient()

# Get notified when servers are found
def on_server(event, server):
    if event == "found":
        print(f"Found: {server.host}:{server.port}")
        print(f"  GPU: {server.has_hw_accel}")

client.add_callback(on_server)
client.start_discovery()

# Wait for discovery
import time
time.sleep(3)

# Use discovered server
if client.is_available():
    server = client.get_server()
    print(f"Using {server.name} at {server.base_url}")
```

### Transcoding Options

```python
# Basic - auto settings
job = await client.transcode(
    source="http://pi:5000/video.mkv"
)

# 720p with specific codec
job = await client.transcode(
    source="http://pi:5000/video.mkv",
    resolution="720p",
    video_codec="h264",
    audio_codec="aac"
)

# Force software encoding (no GPU)
job = await client.transcode(
    source="http://pi:5000/video.mkv",
    hw_accel="software"
)

# Start from specific time (seeking)
job = await client.transcode(
    source="http://pi:5000/video.mkv",
    start_time=300  # Start at 5 minutes
)

# Batch mode (download file when done)
job = await client.transcode(
    source="http://pi:5000/video.mkv",
    mode="batch",
    format="mp4"
)
# job.download_url will be set when ready
```

---

## Configuration

Edit `ghoststream.yaml`:

```yaml
server:
  host: 0.0.0.0      # Listen on all interfaces
  port: 8765         # API port

mdns:
  enabled: true      # Advertise via mDNS
  service_name: "GhostStream Transcoder"

transcoding:
  max_concurrent_jobs: 2    # Parallel transcodes
  segment_duration: 4       # HLS segment length (seconds)
  temp_directory: ./transcode_temp
  # Professional features
  enable_abr: true          # Adaptive bitrate streaming
  abr_max_variants: 4       # Max quality variants (1080p, 720p, 480p, 360p)
  tone_map_hdr: true        # Auto-convert HDR to SDR
  retry_count: 3            # Auto-retry on transient failures
  stall_timeout: 120        # Kill stalled FFmpeg after N seconds

hardware:
  prefer_hw_accel: true     # Use GPU when available
  fallback_to_software: true  # Fall back to CPU if GPU fails
  nvenc_preset: p4          # p1=fast, p7=quality

security:
  api_key: null             # Set to require authentication
```

---

## Reliability Features

GhostStream includes production-grade reliability:

### Auto-Retry
Transient failures (network timeouts, connection resets) automatically retry up to 3 times with exponential backoff.

### Hardware Fallback
If GPU encoding fails (driver issues, unsupported format), automatically falls back to software encoding.

### Stall Detection
FFmpeg processes that stop making progress for 2 minutes are automatically terminated and retried.

### HDR Handling
10-bit HDR content (HDR10, Dolby Vision, HLG) is automatically tone-mapped to SDR for maximum compatibility with H.264 output.

### Quality Ladder (ABR)
Adaptive bitrate uses a professional quality ladder:

| Quality | Resolution | Bitrate |
|---------|------------|---------|
| 4K | 3840x2160 | 20 Mbps |
| 1080p | 1920x1080 | 8 Mbps |
| 720p | 1280x720 | 4 Mbps |
| 480p | 854x480 | 1.5 Mbps |
| 360p | 640x360 | 800 kbps |

---

## Common Use Cases

### 1. Play Incompatible Video in Browser

GhostHub detects video format isn't supported by browser:

```python
# Video is HEVC/MKV - browser can't play it
original_url = "http://pi:5000/media/movie.mkv"

# Request HLS transcode to H.264
job = await client.transcode(
    source=original_url,
    mode="stream",
    video_codec="h264",  # Browser-compatible
    format="hls"
)

# Give this URL to the video player
player_url = job.stream_url
# -> http://192.168.4.2:8765/stream/xxx/master.m3u8
```

### 2. Reduce Quality for Slow Network

```python
job = await client.transcode(
    source="http://pi:5000/media/4k-movie.mkv",
    resolution="720p",  # Downscale
    bitrate="3M"        # Lower bitrate
)
```

### 3. Seek to Specific Time

User clicks on timeline at 45 minutes:

```python
job = await client.transcode(
    source="http://pi:5000/media/movie.mkv",
    start_time=2700  # 45 * 60 seconds
)
```

### 4. Pre-transcode Library (Batch Mode)

```python
for video in library:
    job = await client.transcode(
        source=video.url,
        mode="batch",
        format="mp4",
        resolution="1080p"
    )
    
    # Wait for completion
    job = await client.wait_for_ready(job.job_id, timeout=3600)
    
    if job.status == "ready":
        # Download the transcoded file
        download_url = job.download_url
```

---

## Error Handling

```python
job = await client.transcode(source="http://pi:5000/video.mkv")

if job is None:
    print("Failed to start transcode - server unreachable?")
    return

job = await client.wait_for_ready(job.job_id, timeout=60)

if job.status == "error":
    print(f"Transcode failed: {job.error_message}")
elif job.status == "ready":
    print(f"Success! Stream at: {job.stream_url}")
```

---

---

## Load Balancing (Multiple Servers)

If you have multiple PCs with GPUs, you can distribute transcoding across them.

### Setup Multiple Servers

Run GhostStream on each PC:
```bash
# PC 1 (192.168.4.2)
python -m ghoststream

# PC 2 (192.168.4.3)  
python -m ghoststream

# PC 3 (192.168.4.4)
python -m ghoststream
```

### Use the Load Balancer

```python
from ghoststream import GhostStreamLoadBalancer, LoadBalanceStrategy

# Auto-discover all servers
lb = GhostStreamLoadBalancer(strategy=LoadBalanceStrategy.LEAST_BUSY)
lb.start_discovery()

# Or specify servers manually
lb = GhostStreamLoadBalancer(
    strategy=LoadBalanceStrategy.LEAST_BUSY,
    manual_servers=["192.168.4.2:8765", "192.168.4.3:8765"]
)

# Transcode - automatically picks best server
job = await lb.transcode(
    source="http://pi:5000/video.mkv",
    resolution="1080p"
)
```

### Load Balancing Strategies

| Strategy | Description |
|----------|-------------|
| `LEAST_BUSY` | Pick server with fewest active jobs (default) |
| `FASTEST` | Prefer servers with GPU, then least busy |
| `ROUND_ROBIN` | Rotate through servers evenly |
| `RANDOM` | Random selection |

```python
from ghoststream import GhostStreamLoadBalancer, LoadBalanceStrategy

# Prefer GPU servers
lb = GhostStreamLoadBalancer(strategy=LoadBalanceStrategy.FASTEST)

# Round robin
lb = GhostStreamLoadBalancer(strategy=LoadBalanceStrategy.ROUND_ROBIN)
```

### Batch Transcoding

Transcode your entire library overnight:

```python
# List of videos to transcode
videos = [
    {"source": "http://pi:5000/media/movie1.mkv", "resolution": "1080p"},
    {"source": "http://pi:5000/media/movie2.mkv", "resolution": "1080p"},
    {"source": "http://pi:5000/media/movie3.mkv", "resolution": "720p"},
    {"source": "http://pi:5000/media/movie4.mkv"},
]

# Submit all at once - distributed across servers
jobs = await lb.batch_transcode(videos, parallel=True)

# Get job IDs
job_ids = [j.job_id for j in jobs if j]

# Wait for all to complete
results = await lb.wait_for_all(job_ids, timeout=3600)

for job in results:
    if job and job.status == "ready":
        print(f"Done: {job.download_url}")
    elif job:
        print(f"Failed: {job.error_message}")
```

### Monitor Server Status

```python
# Get all discovered servers
servers = lb.get_servers()
for s in servers:
    print(f"{s.name}: {s.host}:{s.port} (GPU: {s.has_hw_accel})")

# Get real-time stats
stats = lb.get_server_stats()
for name, info in stats.items():
    print(f"{name}: {info['active_jobs']} active, healthy={info['is_healthy']}")
```

---

## Network Requirements

- GhostStream must be able to reach the source URL
- Clients must be able to reach GhostStream's IP:8765
- For mDNS discovery: multicast must work on the network
- No internet required - everything works on LAN

```
Pi (192.168.4.1)          PC (192.168.4.2)
┌─────────────┐           ┌─────────────┐
│  GhostHub   │──source──>│ GhostStream │
│             │<──HLS─────│   (GPU)     │
└─────────────┘           └─────────────┘
```

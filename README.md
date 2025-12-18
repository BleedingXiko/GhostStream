# GhostStream

**Open-Source Video Transcoding Server**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux%20%7C%20Docker-lightgrey.svg)]()

GhostStream is a hardware-accelerated video transcoding server with automatic GPU detection, adaptive bitrate streaming, and minimal configuration. It serves as the transcoding backend for [GhostHub](https://ghosthub.net) but can be used standalone.

## Quick Start

### Pre-built Binaries

| Platform | Download | Run |
|----------|----------|-----|
| Windows | [GhostStream.exe](https://github.com/BleedingXiko/GhostStream/releases/latest) | Double-click |
| Linux | [GhostStream-Linux](https://github.com/BleedingXiko/GhostStream/releases/latest) | `chmod +x && ./GhostStream-Linux` |
| macOS | [GhostStream-macOS](https://github.com/BleedingXiko/GhostStream/releases/latest) | `chmod +x && ./GhostStream-macOS` |

Requires FFmpeg. The application will provide installation instructions if FFmpeg is not found.

### From Source

```bash
git clone https://github.com/BleedingXiko/GhostStream.git
cd GhostStream
python run.py
```

The launcher creates a virtual environment, installs dependencies, and starts the server.

### Docker

```bash
# CPU-only
docker run -d -p 8765:8765 ghcr.io/bleedingxiko/ghoststream

# NVIDIA GPU
docker run -d -p 8765:8765 --gpus all ghcr.io/bleedingxiko/ghoststream:nvidia
```

## Usage

**Python:**
```python
import httpx

response = httpx.post("http://localhost:8765/api/transcode/start", json={
    "source": "https://example.com/video.mp4",
    "mode": "stream"
})
job = response.json()
print(f"Stream URL: {job['stream_url']}")
```

**curl:**
```bash
curl -X POST http://localhost:8765/api/transcode/start \
  -H "Content-Type: application/json" \
  -d '{"source": "https://example.com/video.mp4", "mode": "stream"}'
```

See the `examples/` directory for additional usage examples.

## Features

- **HLS Streaming** - Real-time transcoding with immediate playback
- **Adaptive Bitrate (ABR)** - Multiple quality variants for bandwidth adaptation
- **HDR to SDR** - Automatic tone mapping for HDR content
- **Codec Support** - H.264, H.265/HEVC, VP9, AV1
- **Batch Processing** - Queue multiple files with optional two-pass encoding
- **Hardware Acceleration** - NVIDIA NVENC, Intel QuickSync, AMD AMF, Apple VideoToolbox
- **Automatic Fallback** - Falls back to software encoding if hardware fails
- **Thermal Management** - Reduces load when GPU temperature is high

### Supported Hardware Encoders

| Platform | Encoder | Detection |
|----------|---------|-----------|
| NVIDIA | NVENC | Automatic via nvidia-smi |
| Intel | QuickSync | Automatic via VA-API |
| AMD | AMF/VCE | Automatic |
| Apple | VideoToolbox | Native macOS support |
| CPU | libx264/libx265 | Always available |

## API Reference

### Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/transcode/start` | Start a transcoding job |
| `GET` | `/api/transcode/{id}/status` | Get job status & progress |
| `POST` | `/api/transcode/{id}/cancel` | Cancel a job |
| `DELETE` | `/api/transcode/{id}` | Delete job & cleanup |
| `GET` | `/api/health` | Health check |
| `GET` | `/api/capabilities` | Hardware & codec info |
| `WS` | `/ws/progress` | Real-time progress via WebSocket |

### Start Transcode Request

```json
{
  "source": "https://example.com/video.mp4",
  "mode": "stream",           // "stream", "abr", or "batch"
  "output": {
    "resolution": "720p",     // "4k", "1080p", "720p", "480p", "original"
    "video_codec": "h264",    // "h264", "h265", "vp9", "av1"
    "audio_codec": "aac",     // "aac", "opus", "copy"
    "hw_accel": "auto"        // "auto", "nvenc", "qsv", "software"
  },
  "start_time": 0             // Seek to position (seconds)
}
```

### Response

```json
{
  "job_id": "abc-123",
  "status": "processing",
  "stream_url": "http://localhost:8765/stream/abc-123/master.m3u8",
  "progress": 0
}
```

## Examples

| File | Description |
|------|-------------|
| [`demo.py`](examples/demo.py) | Basic demo with auto-play |
| [`demo.html`](examples/demo.html) | Browser-based demo |
| [`minimal.py`](examples/minimal.py) | Minimal Python example |
| [`quickstart.py`](examples/quickstart.py) | Interactive examples |
| [`curl_examples.md`](examples/curl_examples.md) | HTTP/curl commands |
| [`web_player.html`](examples/web_player.html) | Full-featured web player |

## Configuration

Create `ghoststream.yaml` to customize (optional):

```yaml
server:
  host: 0.0.0.0
  port: 8765

transcoding:
  max_concurrent_jobs: 2
  segment_duration: 4
  tone_map_hdr: true
  retry_count: 3

hardware:
  prefer_hw_accel: true
  fallback_to_software: true
```

## GhostHub Integration

GhostStream serves as the transcoding backend for [GhostHub](https://ghosthub.net).

### Architecture

```
┌─────────────────────────────────┐      ┌─────────────────────────────────┐
│        Raspberry Pi             │      │         Your PC                 │
│  ┌───────────────────────────┐  │      │  ┌───────────────────────────┐  │
│  │       GhostHub            │  │ WiFi │  │      GhostStream          │  │
│  │    (Media Server)         │◄─┼──────┼─►│   (GPU Transcoder)        │  │
│  └───────────────────────────┘  │      │  └───────────────────────────┘  │
└─────────────────────────────────┘      └─────────────────────────────────┘
```

- **Auto-Discovery**: GhostStream advertises via mDNS (`_ghoststream._tcp.local`)
- **On-Demand**: Transcoding occurs only when requested
- **Local Network**: No internet connection required

### Python Client

```python
from ghoststream.client import GhostStreamClient

# Auto-discover on network
client = GhostStreamClient()
client.start_discovery()

# Or connect directly
client = GhostStreamClient(manual_server="192.168.1.100:8765")

# Transcode
if client.is_available():
    job = await client.transcode(
        source="http://pi:5000/media/video.mkv",
        resolution="1080p"
    )
    print(job.stream_url)
```

### WebSocket Progress

```python
# Subscribe to job updates
ws.send({"type": "subscribe", "job_ids": ["job-123"]})

# Receive real-time progress
{"type": "progress", "job_id": "job-123", "data": {"progress": 45.2}}
{"type": "status_change", "job_id": "job-123", "data": {"status": "ready"}}
```

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

```bash
# Development setup
git clone https://github.com/BleedingXiko/GhostStream.git
cd GhostStream
python -m venv venv && source venv/bin/activate  # or venv\Scripts\activate on Windows
pip install -r requirements.txt
python -m ghoststream --log-level DEBUG
```

## License

MIT License - see [LICENSE](LICENSE) for details.

<div align="center">

# 👻 GhostStream

### Enterprise-Grade Adaptive Transcoding Server

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux%20%7C%20Docker-lightgrey.svg)]()
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

**Hardware-accelerated video transcoding with intelligent load balancing, real-time system monitoring, and adaptive quality scaling.**

[Features](#-features) • [Quick Start](#-quick-start) • [API](#-api-endpoints) • [GhostHub Integration](#-first-class-ghosthub-integration) • [Contributing](#-contributing)

</div>

---

## 🎯 What is GhostStream?

GhostStream is a **standalone transcoding server** that can be used:

1. **With GhostHub** (first-class integration) - Automatic discovery, seamless media streaming
2. **Standalone** - REST API for any application needing video transcoding
3. **As a library** - Import directly into your Python projects

Unlike basic FFmpeg wrappers, GhostStream provides **enterprise-grade features**:

- **Adaptive Hardware Profiling** - Automatically detects your hardware capabilities
- **Real-Time Load Balancing** - Dynamically adjusts quality based on system load
- **Thermal Throttling Awareness** - Prevents GPU overheating on laptops
- **Intelligent Queue Management** - Priority-based job scheduling
- **Zero Configuration** - Works out of the box with sensible defaults

---

## ✨ Features

### Core Transcoding
| Feature | Description |
|---------|-------------|
| 🎬 **Live HLS Streaming** | Real-time transcoding with instant playback |
| 📊 **Adaptive Bitrate (ABR)** | Multiple quality variants like Netflix/Plex |
| 🌈 **HDR → SDR Tone Mapping** | Automatic conversion for maximum compatibility |
| 🔄 **Codec Flexibility** | H.264, H.265/HEVC, VP9, AV1 support |
| 📦 **Batch Processing** | Queue multiple files with two-pass encoding |

### Hardware Acceleration
| Platform | Encoder | Auto-Detected |
|----------|---------|---------------|
| **NVIDIA** | NVENC | ✅ via nvidia-smi |
| **Intel** | QuickSync (QSV) | ✅ via VA-API |
| **AMD** | AMF/VCE | ✅ via rocm-smi |
| **Apple** | VideoToolbox | ✅ macOS native |
| **CPU** | libx264/x265 | ✅ Always available |

### 🚀 Enterprise Load Balancing

```
┌─────────────────────────────────────────────────────────────────┐
│                   ADAPTIVE TRANSCODE MANAGER                     │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────┐        │
│  │   Hardware   │   │    System    │   │     Load     │        │
│  │   Profiler   │──▶│   Monitor    │──▶│   Balancer   │        │
│  └──────────────┘   └──────────────┘   └──────────────┘        │
│         │                  │                  │                 │
│         ▼                  ▼                  ▼                 │
│  • GPU VRAM detection    • CPU usage       • Dynamic job limits │
│  • Laptop detection      • GPU temp        • Quality scaling    │
│  • Power source (AC/DC)  • Memory usage    • Priority queuing   │
│  • Hardware tier (1-5)   • Load trends     • Thermal protection │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

**Automatic Hardware Tiers:**

| Tier | Hardware | Max Resolution | Max Bitrate | Concurrent Jobs |
|------|----------|---------------|-------------|-----------------|
| 🏆 **Ultra** | RTX 3080+, 8GB+ VRAM | 4K | 25 Mbps | 4 |
| 🥇 **High** | RTX 3060+, 6GB+ VRAM | 1440p | 15 Mbps | 3 |
| 🥈 **Medium** | GTX 1650+, 4GB+ VRAM | 1080p | 8 Mbps | 2 |
| 🥉 **Low** | Integrated GPU | 720p | 4 Mbps | 1 |
| 📱 **Minimal** | Software only | 480p | 2 Mbps | 1 |

**Smart Adaptations:**
- 🔋 **Battery Mode**: Automatically reduces quality and limits to 1 job
- 🌡️ **Thermal Throttling**: Reduces load when GPU exceeds 80°C
- 📈 **Load Balancing**: Dynamically adjusts based on CPU/GPU utilization
- ⚡ **Power Detection**: Increases capacity when plugged into AC

### Reliability
- ✅ **Auto-Retry** - Transient failures automatically retried
- ✅ **Hardware Fallback** - Falls back to software if GPU fails
- ✅ **Stall Detection** - Kills hung FFmpeg processes
- ✅ **Job History** - Track success rates and performance

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Your Pi (AP Mode)                        │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  GhostHub Media Server                               │   │
│  │  - Serves media files                                │   │
│  │  - Discovers GhostStream via mDNS                    │   │
│  │  - Requests transcoding when needed                  │   │
│  └─────────────────────────────────────────────────────┘   │
│                         │ WiFi AP                           │
└─────────────────────────┼───────────────────────────────────┘
                          │ (No Internet Required)
┌─────────────────────────┼───────────────────────────────────┐
│                    Your PC                                  │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  GhostStream Transcoder                              │   │
│  │  - Advertises via mDNS (_ghoststream._tcp.local)     │   │
│  │  - Uses GPU (NVENC/QSV/AMF) for fast transcoding     │   │
│  │  - Streams HLS back to GhostHub                      │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

**Flow:**
1. PC connects to Pi's WiFi AP
2. GhostStream runs on PC, advertises via mDNS
3. GhostHub on Pi discovers GhostStream automatically
4. When user plays incompatible video, GhostHub requests transcoding
5. GhostStream transcodes using GPU and streams HLS back
6. User watches transcoded video seamlessly

---

## Quick Start

### 📦 Download Pre-Built Executable (Easiest)

No Python needed. Just download and run:

| Platform | Download | Run |
|----------|----------|-----|
| **Windows** | [GhostStream.exe](https://github.com/BleedingXiko/GhostStream/releases/latest/download/GhostStream.exe) | Double-click |
| **Linux** | [GhostStream-Linux-x86_64](https://github.com/BleedingXiko/GhostStream/releases/latest/download/GhostStream-Linux-x86_64) | `chmod +x GhostStream-Linux-x86_64 && ./GhostStream-Linux-x86_64` |
| **macOS** | [GhostStream-macOS](https://github.com/BleedingXiko/GhostStream/releases/latest/download/GhostStream-macOS) | `chmod +x GhostStream-macOS && ./GhostStream-macOS` |

> **Note:** FFmpeg must be installed on your system. The executable will show install instructions if it's missing.

### From Source (Alternative)

```bash
git clone https://github.com/BleedingXiko/GhostStream.git
cd GhostStream
python run.py
```

That's it. The launcher handles everything:
- ✅ Creates virtual environment
- ✅ Installs dependencies
- ✅ Detects FFmpeg (shows install instructions if missing)
- ✅ Starts the server

**Windows users:** Double-click `start.bat`  
**macOS/Linux users:** Run `./start.sh`

### Docker (Alternative)

```bash
# CPU
docker run -d -p 8765:8765 ghcr.io/bleedingxiko/ghoststream

# NVIDIA GPU
docker run -d -p 8765:8765 --gpus all ghcr.io/bleedingxiko/ghoststream:nvidia

# Intel QSV
docker run -d -p 8765:8765 --device /dev/dri ghcr.io/bleedingxiko/ghoststream:intel
```

### Prerequisites

- **Python 3.10+** - [Download](https://python.org/downloads)
- **FFmpeg** - Auto-detected, install instructions shown if missing

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/health` | Health check |
| GET | `/api/capabilities` | Hardware & codec capabilities |
| GET | `/api/stats` | Service statistics |
| POST | `/api/transcode/start` | Start a transcoding job |
| GET | `/api/transcode/{id}/status` | Get job status |
| POST | `/api/transcode/{id}/cancel` | Cancel a job |
| GET | `/api/transcode/{id}/stream` | Stream transcoded output |
| WS | `/ws/progress` | Real-time progress updates (with job subscriptions) |

## Examples

We have examples for every skill level:

| Example | Description |
|---------|-------------|
| [examples/quickstart.py](examples/quickstart.py) | Python examples - run interactively |
| [examples/curl_examples.md](examples/curl_examples.md) | curl/HTTP - no coding required |
| [examples/web_player.html](examples/web_player.html) | Browser player with hls.js |
| [examples/ghosthub_integration.py](examples/ghosthub_integration.py) | Full GhostHub integration |

### Quick curl Example

```bash
# Start transcode
curl -X POST http://localhost:8765/api/transcode/start \
  -H "Content-Type: application/json" \
  -d '{"source": "https://example.com/video.mp4", "mode": "stream"}'

# Response: {"job_id": "abc123", "stream_url": "http://localhost:8765/stream/abc123/master.m3u8", "duration": 3600.5}

# Check status
curl http://localhost:8765/api/transcode/abc123/status

# Play when ready
vlc http://localhost:8765/stream/abc123/master.m3u8
```

### Quick Python Example

```python
import httpx

# Start transcode
resp = httpx.post("http://localhost:8765/api/transcode/start", json={
    "source": "https://example.com/video.mp4",
    "mode": "stream",
    "output": {"resolution": "720p"}
})
job = resp.json()
print(f"Stream URL: {job['stream_url']}")
```

### Adaptive Bitrate (ABR)

Netflix-style multiple quality variants:

```bash
curl -X POST http://localhost:8765/api/transcode/start \
  -H "Content-Type: application/json" \
  -d '{"source": "https://example.com/4k-movie.mp4", "mode": "abr"}'
```

ABR auto-generates quality variants (1080p, 720p, 480p) based on source resolution.

## Configuration

Edit `ghoststream.yaml` to customize:

```yaml
server:
  host: 0.0.0.0
  port: 8765

transcoding:
  max_concurrent_jobs: 2
  segment_duration: 4
  enable_abr: true           # Adaptive bitrate streaming
  abr_max_variants: 4        # Quality levels (1080p, 720p, 480p, 360p)
  tone_map_hdr: true         # Auto-convert HDR to SDR
  retry_count: 3             # Auto-retry on failures
  stall_timeout: 120         # Kill stalled FFmpeg after 2 min

hardware:
  prefer_hw_accel: true
  fallback_to_software: true # Fall back if GPU fails
  nvenc_preset: p4           # p1=fast, p7=quality
```

## Hardware Acceleration

GhostStream automatically detects available hardware encoders:

| Platform | Encoder | Detection |
|----------|---------|-----------|
| NVIDIA | NVENC | nvidia-smi + CUDA |
| Intel | QuickSync | VA-API/QSV drivers |
| AMD | AMF/VCE | amf encoder check |
| Apple | VideoToolbox | macOS only |
| CPU | libx264/libx265 | Always available |

## Integration with GhostHub

### Python Client

```python
from ghoststream.client import GhostStreamClient

# Auto-discover GhostStream on the network
client = GhostStreamClient()
client.start_discovery()

# Or connect manually if mDNS doesn't work
client = GhostStreamClient(manual_server="192.168.4.2:8765")

# Check if available
if client.is_available():
    # Request transcoding
    job = await client.transcode(
        source="http://192.168.4.1:5000/media/video.mkv",
        resolution="1080p",
        hw_accel="auto"
    )
    
    # Get the stream URL for playback
    print(job.stream_url)
    # -> http://192.168.4.2:8765/stream/job-id/master.m3u8
```

### mDNS Discovery

GhostStream advertises as `_ghoststream._tcp.local` with properties:
- `version` - API version
- `hw_accels` - Available hardware acceleration (nvenc, qsv, etc.)
- `video_codecs` - Supported codecs (h264, h265, vp9, av1)
- `max_jobs` - Max concurrent transcodes

### Manual Integration

If mDNS doesn't work on your network:

```python
# In GhostHub settings, let users specify GhostStream IP:
GHOSTSTREAM_SERVER = "192.168.4.2:8765"

# Then connect directly:
client = GhostStreamClient(manual_server=GHOSTSTREAM_SERVER)
```

## 🔗 First-Class GhostHub Integration

GhostStream is designed as the **official transcoding backend for GhostHub**. When used together:

- **Zero Configuration** - mDNS auto-discovery, no IP addresses to configure
- **Seamless Playback** - Incompatible videos automatically transcoded
- **Offline Operation** - Works entirely on your local network, no internet needed
- **Resource Aware** - GhostHub knows GhostStream's capabilities and limits
- **Real-Time Updates** - WebSocket push for instant progress (no polling)

### Communication Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    GhostHub (Pi)                            │
│  ┌───────────────┐      ┌───────────────┐                  │
│  │  HTTP Client  │      │ WebSocket Client│                │
│  │  (API calls)  │      │ (progress push) │                │
│  └───────┬───────┘      └───────┬────────┘                 │
└──────────┼──────────────────────┼──────────────────────────┘
           │                      │
           │ REST API             │ WS /ws/progress
           │ (start, status,      │ (subscribe to jobs,
           │  cancel)             │  receive updates)
           ▼                      ▼
┌─────────────────────────────────────────────────────────────┐
│                  GhostStream (PC with GPU)                  │
│  ┌──────────┐  ┌──────────┐  ┌──────────────────┐          │
│  │ REST API │  │WebSocket │  │ Transcode Engine │          │
│  │ (FastAPI)│  │ Manager  │  │ (FFmpeg + HW)    │          │
│  └──────────┘  └──────────┘  └──────────────────┘          │
└─────────────────────────────────────────────────────────────┘
```

### WebSocket Progress (Recommended)

```python
# Subscribe to specific jobs only (efficient)
ws.send({"type": "subscribe", "job_ids": ["job-123"]})

# Receive real-time progress
{"type": "progress", "job_id": "job-123", "data": {"progress": 45.2}}
{"type": "status_change", "job_id": "job-123", "data": {"status": "ready"}}
```

---

## 📊 System Status API

Get real-time system status including hardware tier, load, and job statistics:

```bash
curl http://localhost:8765/api/status
```

```json
{
  "hardware": {
    "tier": "medium",
    "gpu_name": "NVIDIA GeForce GTX 1660",
    "gpu_vram_mb": 6144,
    "is_laptop": false,
    "power_source": "ac",
    "max_resolution": [1920, 1080],
    "recommended_encoder": "h264_nvenc"
  },
  "realtime": {
    "cpu_percent": 23.5,
    "gpu_percent": 45.2,
    "gpu_temperature_c": 62,
    "load_factor": 0.42,
    "is_overloaded": false,
    "load_trend": "stable"
  },
  "jobs": {
    "active_jobs": 1,
    "queued_jobs": 0,
    "max_concurrent_jobs": 2,
    "quality_factor": 1.0,
    "recent_success_rate": 0.98
  }
}
```

---

## 🤝 Contributing

We welcome contributions! GhostStream is open source and community-driven.

### Ways to Contribute

- 🐛 **Report Bugs** - Open an issue with reproduction steps
- 💡 **Feature Requests** - Suggest new features or improvements
- 📖 **Documentation** - Help improve docs and examples
- 🔧 **Code** - Submit pull requests for bug fixes or features

See [CONTRIBUTING.md](CONTRIBUTING.md) for detailed guidelines.

### Development Setup

```bash
# Clone and setup
git clone https://github.com/yourusername/ghoststream.git
cd ghoststream
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Run tests
pytest

# Run with debug logging
python -m ghoststream --log-level DEBUG
```

---

## 📜 License

MIT License - see [LICENSE](LICENSE) file for details.

---

## 🙏 Acknowledgments

- **FFmpeg** - The amazing multimedia framework that powers transcoding
- **FastAPI** - Modern, fast web framework for the API
- **zeroconf** - mDNS/DNS-SD discovery

---

<div align="center">

**[⬆ Back to Top](#-ghoststream)**

Made with ❤️ for the open source community

</div>

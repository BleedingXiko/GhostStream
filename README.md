<div align="center">

# 👻 GhostStream

### Open-Source Video Transcoding Server

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux%20%7C%20Docker-lightgrey.svg)]()
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

**Hardware-accelerated video transcoding with automatic GPU detection, adaptive quality, and zero configuration.**

[Quick Start](#-quick-start) • [Features](#-features) • [API](#-api-reference) • [Examples](#-examples) • [GhostHub](#-ghosthub-integration)

</div>

---

## ⚡ Quick Start

### Option 1: Download & Run (Easiest)

| Platform | Download | Run |
|----------|----------|-----|
| **Windows** | [GhostStream.exe](https://github.com/BleedingXiko/GhostStream/releases/latest) | Double-click |
| **Linux** | [GhostStream-Linux](https://github.com/BleedingXiko/GhostStream/releases/latest) | `chmod +x && ./GhostStream-Linux` |
| **macOS** | [GhostStream-macOS](https://github.com/BleedingXiko/GhostStream/releases/latest) | `chmod +x && ./GhostStream-macOS` |

> Requires FFmpeg installed. The app will show install instructions if missing.

### Option 2: From Source

```bash
git clone https://github.com/BleedingXiko/GhostStream.git
cd GhostStream
python run.py
```

That's it! The launcher auto-creates a venv, installs dependencies, and starts the server.

### Option 3: Docker

```bash
docker run -d -p 8765:8765 ghcr.io/bleedingxiko/ghoststream          # CPU
docker run -d -p 8765:8765 --gpus all ghcr.io/bleedingxiko/ghoststream:nvidia  # NVIDIA
```

---

## 🎬 Try It Now

Once running, test it immediately:

```bash
# Terminal 1: Start GhostStream
python run.py

# Terminal 2: Run demo (auto-opens player)
python examples/demo.py
```

Or open `examples/demo.html` in your browser for a one-click web demo.

**Minimal Python example:**
```python
import httpx

job = httpx.post("http://localhost:8765/api/transcode/start", json={
    "source": "https://example.com/video.mp4",
    "mode": "stream"
}).json()

print(f"Play: {job['stream_url']}")
```

---

## ✨ Features

| Feature | Description |
|---------|-------------|
| 🎬 **Live HLS Streaming** | Real-time transcoding with instant playback |
| 📊 **Adaptive Bitrate (ABR)** | Netflix-style multiple quality variants |
| 🌈 **HDR → SDR** | Automatic tone mapping for compatibility |
| 🔄 **Codec Support** | H.264, H.265/HEVC, VP9, AV1 |
| 📦 **Batch Processing** | Queue multiple files with two-pass encoding |
| ⚡ **Hardware Acceleration** | NVIDIA NVENC, Intel QSV, AMD AMF, Apple VideoToolbox |
| 🔁 **Auto-Retry & Fallback** | Falls back to CPU if GPU fails |
| 🌡️ **Thermal Awareness** | Reduces load when GPU overheats |

### Hardware Auto-Detection

GhostStream automatically detects and uses the best available encoder:

| Platform | Encoder | Detection |
|----------|---------|-----------|
| NVIDIA | NVENC | ✅ Auto via nvidia-smi |
| Intel | QuickSync | ✅ Auto via VA-API |
| AMD | AMF/VCE | ✅ Auto |
| Apple | VideoToolbox | ✅ macOS native |
| CPU | libx264/x265 | ✅ Always available |

---

## 📖 API Reference

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

---

## 📂 Examples

| File | Description |
|------|-------------|
| [`demo.py`](examples/demo.py) | **Start here!** Zero-config demo with auto-play |
| [`demo.html`](examples/demo.html) | One-click web demo |
| [`minimal.py`](examples/minimal.py) | 10-line copy-paste example |
| [`quickstart.py`](examples/quickstart.py) | Interactive Python examples |
| [`curl_examples.md`](examples/curl_examples.md) | curl/HTTP commands |
| [`web_player.html`](examples/web_player.html) | Full-featured browser player |

### curl Example

```bash
# Start transcode
curl -X POST http://localhost:8765/api/transcode/start \
  -H "Content-Type: application/json" \
  -d '{"source": "https://example.com/video.mp4", "mode": "stream"}'

# Play in VLC
vlc http://localhost:8765/stream/{job_id}/master.m3u8
```

---

## ⚙️ Configuration

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

---

## 🔗 GhostHub Integration

GhostStream is designed as the **official transcoding backend for [GhostHub Pi](https://ghosthub.net)**. 

### How It Works

```
┌─────────────────────────────────┐      ┌─────────────────────────────────┐
│        Raspberry Pi             │      │         Your PC                 │
│  ┌───────────────────────────┐  │      │  ┌───────────────────────────┐  │
│  │       GhostHub            │  │ WiFi │  │      GhostStream          │  │
│  │    (Media Server)         │◄─┼──────┼─►│   (GPU Transcoder)        │  │
│  └───────────────────────────┘  │      │  └───────────────────────────┘  │
└─────────────────────────────────┘      └─────────────────────────────────┘
```

1. **Auto-Discovery**: GhostStream advertises via mDNS (`_ghoststream._tcp.local`)
2. **On-Demand**: GhostHub requests transcoding only when needed
3. **Seamless**: User plays video → automatically transcoded → streams back
4. **Offline**: Works entirely on local network, no internet required

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

---

## 🤝 Contributing

We welcome contributions!

- 🐛 **Report Bugs** - Open an issue with reproduction steps
- 💡 **Feature Requests** - Suggest improvements
- 🔧 **Code** - Submit pull requests

```bash
# Development setup
git clone https://github.com/BleedingXiko/GhostStream.git
cd GhostStream
python -m venv venv && source venv/bin/activate  # or venv\Scripts\activate on Windows
pip install -r requirements.txt
python -m ghoststream --log-level DEBUG
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

---

## 📜 License

MIT License - see [LICENSE](LICENSE) for details.

---

<div align="center">

**[⬆ Back to Top](#-ghoststream)**

Made with ❤️ for the open source community

</div>

#!/usr/bin/env python3
"""
GhostStream Minimal Example - Transcode in 10 Lines
====================================================

Copy this into your project to get started instantly.

Prerequisites:
    1. GhostStream running: python run.py
    2. httpx installed: pip install httpx

Usage:
    python examples/minimal.py
"""

import httpx

# Any video URL works - HTTP, RTSP, S3, etc.
VIDEO_URL = "https://test-videos.co.uk/vids/bigbuckbunny/mp4/h264/720/Big_Buck_Bunny_720_10s_1MB.mp4"

# Start transcode - returns immediately with stream URL
response = httpx.post("http://localhost:8765/api/transcode/start", json={
    "source": VIDEO_URL,
    "mode": "stream",  # "stream" for HLS, "abr" for adaptive bitrate
    "output": {"resolution": "720p"}
}, timeout=30)

job = response.json()

print(f"🎬 Job ID: {job['job_id']}")
print(f"📺 Stream URL: {job['stream_url']}")
print(f"\n▶ Play with VLC:")
print(f"   vlc {job['stream_url']}")
print(f"\n▶ Or check status:")
print(f"   curl http://localhost:8765/api/transcode/{job['job_id']}/status")

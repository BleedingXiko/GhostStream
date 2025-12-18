#!/usr/bin/env python3
"""
GhostStream Minimal Example - Transcode in 10 Lines
====================================================

Copy this into your project to get started instantly.

Prerequisites:
    1. GhostStream running: python run.py
    2. Install SDK: pip install ghoststream

Usage:
    python examples/minimal.py
"""

from ghoststream import GhostStreamClient, TranscodeStatus

# Any video URL works - HTTP, RTSP, S3, etc.
VIDEO_URL = "https://test-videos.co.uk/vids/bigbuckbunny/mp4/h264/720/Big_Buck_Bunny_720_10s_1MB.mp4"

# Create client with manual server (or use mDNS discovery)
client = GhostStreamClient(manual_server="localhost:8765")

# Start transcode - returns immediately with stream URL
job = client.transcode_sync(source=VIDEO_URL, resolution="720p")

if job.status == TranscodeStatus.ERROR:
    print(f"❌ Error: {job.error_message}")
else:
    print(f"🎬 Job ID: {job.job_id}")
    print(f"📺 Stream URL: {job.stream_url}")
    print(f"\n▶ Play with VLC:")
    print(f"   vlc {job.stream_url}")
    print(f"\n▶ Or check status:")
    status = client.get_job_status_sync(job.job_id)
    print(f"   Status: {status.status.value}, Progress: {status.progress}%")

#!/usr/bin/env python3
"""
GhostStream Quick Start Examples
================================

Copy-paste examples to get started in 30 seconds.
No GhostHub required - works with any video source.

Run GhostStream first:
    python run.py

Then run these examples:
    python examples/quickstart.py
"""

import time
import httpx

# Change this to your GhostStream server
GHOSTSTREAM_URL = "http://localhost:8765"


# =============================================================================
# EXAMPLE 1: Transcode a URL to HLS stream
# =============================================================================
def example_url_to_hls():
    """
    Transcode any video URL to HLS for web playback.
    Works with: HTTP URLs, RTSP streams, S3 URLs, etc.
    """
    print("\n🎬 Example 1: URL to HLS Stream")
    print("-" * 40)
    
    # Any video URL works - HTTP, RTSP, S3, etc.
    video_url = "https://test-videos.co.uk/vids/bigbuckbunny/mp4/h264/1080/Big_Buck_Bunny_1080_10s_1MB.mp4"
    
    response = httpx.post(
        f"{GHOSTSTREAM_URL}/api/transcode/start",
        json={
            "source": video_url,
            "mode": "stream",  # HLS streaming
            "output": {
                "resolution": "720p",
                "video_codec": "h264"
            }
        },
        timeout=30
    )
    
    if response.status_code != 200:
        print(f"❌ Error: {response.text}")
        return
    
    job = response.json()
    job_id = job["job_id"]
    print(f"✅ Job started: {job_id}")
    
    # Wait for stream to be ready
    print("   Waiting for transcode...")
    for _ in range(30):
        status = httpx.get(f"{GHOSTSTREAM_URL}/api/transcode/{job_id}/status").json()
        if status["status"] == "ready":
            print(f"✅ Stream ready!")
            print(f"   URL: {status['stream_url']}")
            print(f"\n   Play with VLC: vlc {status['stream_url']}")
            print(f"   Or in browser with hls.js")
            break
        elif status["status"] == "error":
            print(f"❌ Error: {status.get('error')}")
            break
        time.sleep(1)
    
    # Cleanup
    input("\nPress Enter to cleanup...")
    httpx.delete(f"{GHOSTSTREAM_URL}/api/transcode/{job_id}")
    print("✅ Cleaned up")


# =============================================================================
# EXAMPLE 2: Transcode local file (via file server)
# =============================================================================
def example_local_file():
    """
    Transcode a local file by serving it over HTTP.
    
    Option A: Use Python's built-in server:
        cd /path/to/videos && python -m http.server 8000
        
    Option B: Use any web server (nginx, Apache, etc.)
    """
    print("\n🎬 Example 2: Local File via HTTP")
    print("-" * 40)
    print("""
    To transcode local files, serve them over HTTP first:
    
    1. Open a new terminal
    2. cd to your video folder
    3. Run: python -m http.server 8000
    4. Your video is now at: http://localhost:8000/video.mp4
    
    Then use that URL with GhostStream.
    """)


# =============================================================================
# EXAMPLE 3: Adaptive Bitrate (multiple qualities)
# =============================================================================
def example_adaptive_bitrate():
    """
    Create Netflix-style adaptive streaming with multiple quality levels.
    Player automatically switches quality based on bandwidth.
    """
    print("\n🎬 Example 3: Adaptive Bitrate (ABR)")
    print("-" * 40)
    
    video_url = "https://test-videos.co.uk/vids/bigbuckbunny/mp4/h264/1080/Big_Buck_Bunny_1080_10s_1MB.mp4"
    
    response = httpx.post(
        f"{GHOSTSTREAM_URL}/api/transcode/start",
        json={
            "source": video_url,
            "mode": "abr",  # Adaptive bitrate - creates 1080p, 720p, 480p variants
            "output": {
                "video_codec": "h264"
            }
        },
        timeout=30
    )
    
    if response.status_code != 200:
        print(f"❌ Error: {response.text}")
        return
    
    job = response.json()
    job_id = job["job_id"]
    print(f"✅ ABR job started: {job_id}")
    print("   Creating quality variants: 1080p, 720p, 480p...")
    
    # Wait for ready
    for _ in range(60):
        status = httpx.get(f"{GHOSTSTREAM_URL}/api/transcode/{job_id}/status").json()
        if status["status"] == "ready":
            print(f"✅ ABR stream ready!")
            print(f"   Master playlist: {status['stream_url']}")
            print(f"\n   The master.m3u8 contains all quality variants.")
            print(f"   HLS players (VLC, hls.js) auto-select best quality.")
            break
        elif status["status"] == "error":
            print(f"❌ Error: {status.get('error')}")
            break
        time.sleep(1)
    
    input("\nPress Enter to cleanup...")
    httpx.delete(f"{GHOSTSTREAM_URL}/api/transcode/{job_id}")


# =============================================================================
# EXAMPLE 4: Check hardware capabilities
# =============================================================================
def example_check_hardware():
    """
    See what hardware acceleration is available.
    """
    print("\n🎬 Example 4: Hardware Capabilities")
    print("-" * 40)
    
    response = httpx.get(f"{GHOSTSTREAM_URL}/api/capabilities")
    if response.status_code != 200:
        print(f"❌ Error: {response.text}")
        return
    
    caps = response.json()
    
    print(f"FFmpeg: {caps.get('ffmpeg_version', 'unknown')}")
    print(f"Platform: {caps.get('platform', 'unknown')}")
    print(f"\nHardware Acceleration:")
    
    for hw in caps.get("hw_accels", []):
        status = "✅" if hw.get("available") else "❌"
        print(f"  {status} {hw.get('type', 'unknown').upper()}")
        if hw.get("available") and hw.get("encoders"):
            print(f"      Encoders: {', '.join(hw['encoders'][:3])}")
    
    print(f"\nVideo Codecs: {', '.join(caps.get('video_codecs', []))}")
    print(f"Audio Codecs: {', '.join(caps.get('audio_codecs', []))}")


# =============================================================================
# EXAMPLE 5: Simple health check
# =============================================================================
def example_health_check():
    """
    Check if GhostStream is running and healthy.
    """
    print("\n🎬 Example 5: Health Check")
    print("-" * 40)
    
    try:
        response = httpx.get(f"{GHOSTSTREAM_URL}/api/health", timeout=5)
        if response.status_code == 200:
            data = response.json()
            print(f"✅ GhostStream is healthy")
            print(f"   Version: {data.get('version')}")
            print(f"   Uptime: {data.get('uptime_seconds', 0):.0f}s")
            print(f"   Active jobs: {data.get('current_jobs', 0)}")
            print(f"   Queued jobs: {data.get('queued_jobs', 0)}")
        else:
            print(f"❌ Unhealthy: {response.status_code}")
    except httpx.ConnectError:
        print(f"❌ Cannot connect to {GHOSTSTREAM_URL}")
        print(f"   Make sure GhostStream is running: python run.py")


# =============================================================================
# EXAMPLE 6: Start from specific time (seeking)
# =============================================================================
def example_seeking():
    """
    Start transcoding from a specific timestamp.
    Useful for resume playback.
    """
    print("\n🎬 Example 6: Seek to Timestamp")
    print("-" * 40)
    
    video_url = "https://test-videos.co.uk/vids/bigbuckbunny/mp4/h264/1080/Big_Buck_Bunny_1080_10s_1MB.mp4"
    
    response = httpx.post(
        f"{GHOSTSTREAM_URL}/api/transcode/start",
        json={
            "source": video_url,
            "mode": "stream",
            "start_time": 5,  # Start from 5 seconds
            "output": {
                "resolution": "720p"
            }
        },
        timeout=30
    )
    
    if response.status_code == 200:
        job = response.json()
        print(f"✅ Started from 5 seconds: {job['job_id']}")
        
        # Cleanup after a moment
        time.sleep(3)
        httpx.delete(f"{GHOSTSTREAM_URL}/api/transcode/{job['job_id']}")
        print("✅ Cleaned up")
    else:
        print(f"❌ Error: {response.text}")


# =============================================================================
# MAIN
# =============================================================================
if __name__ == "__main__":
    print("=" * 50)
    print("GhostStream Quick Start Examples")
    print("=" * 50)
    print(f"\nServer: {GHOSTSTREAM_URL}")
    print("\nMake sure GhostStream is running first:")
    print("  python run.py")
    print("\n" + "=" * 50)
    
    # Check server first
    example_health_check()
    
    print("\n" + "=" * 50)
    print("Select an example to run:")
    print("  1. URL to HLS Stream")
    print("  2. Local File (instructions)")
    print("  3. Adaptive Bitrate (ABR)")
    print("  4. Hardware Capabilities")
    print("  5. Health Check")
    print("  6. Seek to Timestamp")
    print("  0. Run all")
    print("=" * 50)
    
    choice = input("\nEnter choice (1-6, or 0 for all): ").strip()
    
    if choice == "1":
        example_url_to_hls()
    elif choice == "2":
        example_local_file()
    elif choice == "3":
        example_adaptive_bitrate()
    elif choice == "4":
        example_check_hardware()
    elif choice == "5":
        example_health_check()
    elif choice == "6":
        example_seeking()
    elif choice == "0":
        example_health_check()
        example_check_hardware()
        example_url_to_hls()
        example_adaptive_bitrate()
        example_seeking()
    else:
        print("Invalid choice")

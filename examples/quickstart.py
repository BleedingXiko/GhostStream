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
from ghoststream import GhostStreamClient, TranscodeStatus

# Change this to your GhostStream server
GHOSTSTREAM_SERVER = "localhost:8765"

# Create a shared client instance
client = GhostStreamClient(manual_server=GHOSTSTREAM_SERVER)


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
    
    # Start transcoding using SDK
    job = client.transcode(
        source=video_url,
        mode="stream",
        resolution="720p",
        video_codec="h264"
    )
    
    if job.status == TranscodeStatus.ERROR:
        print(f"❌ Error: {job.error_message}")
        return
    
    print(f"✅ Job started: {job.job_id}")
    
    # Wait for stream to be ready
    print("   Waiting for transcode...")
    result = client.wait_for_ready(job.job_id, timeout=30)
    
    if result and result.status == TranscodeStatus.READY:
        print(f"✅ Stream ready!")
        print(f"   URL: {result.stream_url}")
        print(f"\n   Play with VLC: vlc {result.stream_url}")
        print(f"   Or in browser with hls.js")
    elif result and result.status == TranscodeStatus.ERROR:
        print(f"❌ Error: {result.error_message}")
    elif result and result.stream_url:
        print(f"✅ Stream available (still processing)!")
        print(f"   URL: {result.stream_url}")
    
    # Cleanup
    input("\nPress Enter to cleanup...")
    client.delete_job(job.job_id)
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
    
    # Start ABR transcoding using SDK
    job = client.transcode(
        source=video_url,
        mode="abr",  # Adaptive bitrate - creates 1080p, 720p, 480p variants
        video_codec="h264"
    )
    
    if job.status == TranscodeStatus.ERROR:
        print(f"❌ Error: {job.error_message}")
        return
    
    print(f"✅ ABR job started: {job.job_id}")
    print("   Creating quality variants: 1080p, 720p, 480p...")
    
    # Wait for ready
    result = client.wait_for_ready(job.job_id, timeout=60)
    
    if result and result.status == TranscodeStatus.READY:
        print(f"✅ ABR stream ready!")
        print(f"   Master playlist: {result.stream_url}")
        print(f"\n   The master.m3u8 contains all quality variants.")
        print(f"   HLS players (VLC, hls.js) auto-select best quality.")
    elif result and result.status == TranscodeStatus.ERROR:
        print(f"❌ Error: {result.error_message}")
    elif result and result.stream_url:
        print(f"✅ ABR stream available!")
        print(f"   Master playlist: {result.stream_url}")
    
    input("\nPress Enter to cleanup...")
    client.delete_job(job.job_id)


# =============================================================================
# EXAMPLE 4: Check hardware capabilities
# =============================================================================
def example_check_hardware():
    """
    See what hardware acceleration is available.
    """
    print("\n🎬 Example 4: Hardware Capabilities")
    print("-" * 40)
    
    # Get capabilities using SDK
    caps = client.get_capabilities()
    
    if not caps:
        print("❌ Error: Could not get capabilities")
        return
    
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
    
    # Check health using SDK
    if client.health_check():
        print(f"✅ GhostStream is healthy")
        # Get more details from capabilities
        caps = client.get_capabilities()
        if caps:
            print(f"   Version: {caps.get('version', 'unknown')}")
            print(f"   Platform: {caps.get('platform', 'unknown')}")
    else:
        print(f"❌ Cannot connect to GhostStream at {GHOSTSTREAM_SERVER}")
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
    
    # Start transcoding from specific timestamp using SDK
    job = client.transcode(
        source=video_url,
        mode="stream",
        start_time=5,  # Start from 5 seconds
        resolution="720p"
    )
    
    if job.status != TranscodeStatus.ERROR:
        print(f"✅ Started from 5 seconds: {job.job_id}")
        
        # Cleanup after a moment
        time.sleep(3)
        client.delete_job(job.job_id)
        print("✅ Cleaned up")
    else:
        print(f"❌ Error: {job.error_message}")


# =============================================================================
# MAIN
# =============================================================================
if __name__ == "__main__":
    print("=" * 50)
    print("GhostStream Quick Start Examples")
    print("=" * 50)
    print(f"\nServer: {GHOSTSTREAM_SERVER}")
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

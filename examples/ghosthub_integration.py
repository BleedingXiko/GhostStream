"""
GhostHub + GhostStream Integration Example
==========================================

This shows how GhostHub (running on your Pi) can discover and use
GhostStream (running on your PC) for professional-grade transcoding.

Architecture:
    Pi (AP Mode) - runs GhostHub media server
         |
    WiFi Connection (no internet needed)
         |
    PC - runs GhostStream with GPU transcoding

Features demonstrated:
    - mDNS auto-discovery
    - Single-quality HLS streaming
    - Adaptive Bitrate (ABR) streaming with multiple qualities
    - HDR to SDR tone mapping
    - Hardware acceleration with automatic fallback
    - Job lifecycle management and cleanup
    - Seeking/resume support
    - Progress tracking with the synchronous polling API

Communication Methods:
    - HTTP REST: API calls (start job, get status, cancel)
    - Browser/JS clients can also use `/ws/progress` directly
    - mDNS/UDP: Server discovery on LAN

SDK Installation:
    pip install ghoststream
    # or from source: pip install -e .
"""

import logging
from typing import Optional, Dict, Any, List

# Import GhostStream SDK
from ghoststream import (
    GhostStreamClient,
    GhostStreamServer,
    GhostStreamLoadBalancer,
    TranscodeJob,
    TranscodeStatus,
    ClientConfig,
    LoadBalanceStrategy,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============== Example Usage ==============

def example_basic_streaming():
    """
    Example 1: Basic HLS Streaming
    
    The simplest way to transcode - single quality, fast startup.
    """
    print("\n" + "="*60)
    print("Example 1: Basic HLS Streaming")
    print("="*60)
    
    # Create SDK client with manual server address
    client = GhostStreamClient(manual_server="192.168.4.2:8765")
    
    if not client.health_check():
        print("❌ GhostStream not reachable")
        return
    
    print("✅ GhostStream is online")
    
    # Start transcoding using SDK
    job = client.transcode(
        source="http://192.168.4.1:5000/media/movie.mkv",
        mode="stream",
        resolution="1080p"
    )
    
    if job.status == TranscodeStatus.ERROR:
        print(f"❌ Failed to start transcoding: {job.error_message}")
        return
    
    print(f"✅ Job started: {job.job_id}")
    print(f"   HW Accel: {job.hw_accel_used or 'pending'}")
    
    # Wait for stream to be ready
    result = client.wait_for_ready(job.job_id, timeout=30)
    
    if result and result.status == TranscodeStatus.READY:
        print(f"✅ Stream ready!")
        print(f"   URL: {result.stream_url}")
        print(f"\n   Play with: ffplay '{result.stream_url}'")
    elif result and result.stream_url:
        print(f"✅ Stream available (processing)!")
        print(f"   URL: {result.stream_url}")
    else:
        print(f"❌ Transcoding failed: {result}")
    
    # Cleanup when done
    input("\nPress Enter to cleanup...")
    client.delete_job(job.job_id)
    print("✅ Cleaned up")


def example_abr_streaming():
    """
    Example 2: Adaptive Bitrate (ABR) Streaming
    
    Multiple quality variants - player can switch based on bandwidth.
    Like Plex/Jellyfin/Netflix.
    """
    print("\n" + "="*60)
    print("Example 2: Adaptive Bitrate (ABR) Streaming")
    print("="*60)
    
    client = GhostStreamClient(manual_server="192.168.4.2:8765")
    
    if not client.health_check():
        print("❌ GhostStream not reachable")
        return
    
    # Check capabilities using SDK
    caps = client.get_capabilities()
    if caps:
        print(f"✅ Server capabilities:")
        print(f"   Video codecs: {caps.get('video_codecs', [])}")
        print(f"   HW acceleration: {caps.get('hw_accels', [])}")
    
    # Start ABR transcoding (multiple quality variants)
    job = client.transcode(
        source="http://192.168.4.1:5000/media/4k-hdr-movie.mkv",
        mode="abr"
    )
    
    if job.status == TranscodeStatus.ERROR:
        print(f"❌ Failed to start ABR transcoding: {job.error_message}")
        return
    
    print(f"✅ ABR job started: {job.job_id}")
    
    # Wait for stream
    result = client.wait_for_ready(job.job_id, timeout=60)
    
    if result and result.status == TranscodeStatus.READY:
        print(f"✅ ABR stream ready!")
        print(f"   Master playlist: {result.stream_url}")
        print(f"\n   The master.m3u8 contains multiple quality variants.")
        print(f"   HLS players automatically select best quality.")
    elif result and result.stream_url:
        print(f"✅ ABR stream available!")
        print(f"   Master playlist: {result.stream_url}")
    else:
        print(f"❌ ABR transcoding failed: {result}")
    
    # Cleanup
    input("\nPress Enter to cleanup...")
    client.delete_job(job.job_id)


def example_hdr_content():
    """
    Example 3: HDR Content Handling
    
    GhostStream automatically tone-maps HDR to SDR for compatibility.
    """
    print("\n" + "="*60)
    print("Example 3: HDR to SDR Tone Mapping")
    print("="*60)
    
    client = GhostStreamClient(manual_server="192.168.4.2:8765")
    
    # HDR content (10-bit HEVC with HDR10/Dolby Vision)
    # GhostStream detects this and applies tone mapping automatically
    job = client.transcode(
        source="http://192.168.4.1:5000/media/hdr-demo.mkv",
        mode="stream",
        resolution="1080p",
        video_codec="h264",  # H264 doesn't support HDR, so tone mapping is applied
        tone_map=True
    )
    
    if job.status != TranscodeStatus.ERROR:
        print(f"✅ Job started with automatic HDR detection")
        print(f"   Tone mapping will be applied if source is HDR")
        
        result = client.wait_for_ready(job.job_id, timeout=60)
        if result:
            print(f"   Result: {result.status.value}")
            if result.stream_url:
                print(f"   Stream: {result.stream_url}")
        
        client.delete_job(job.job_id)
    else:
        print(f"❌ Failed: {job.error_message}")


def example_seeking():
    """
    Example 4: Seeking/Resume Playback
    
    Start transcoding from a specific position.
    """
    print("\n" + "="*60)
    print("Example 4: Seeking (Start from 30 minutes)")
    print("="*60)
    
    client = GhostStreamClient(manual_server="192.168.4.2:8765")
    
    # Start from 30 minutes (1800 seconds)
    job = client.transcode(
        source="http://192.168.4.1:5000/media/movie.mkv",
        mode="stream",
        resolution="720p",
        start_time=1800  # 30 minutes in seconds
    )
    
    if job.status != TranscodeStatus.ERROR:
        print(f"✅ Started transcoding from 30:00")
        result = client.wait_for_ready(job.job_id)
        if result and result.stream_url:
            print(f"   Stream: {result.stream_url}")
        client.delete_job(job.job_id)
    else:
        print(f"❌ Failed: {job.error_message}")


def example_batch_transcode():
    """
    Example 5: Batch Transcoding (File-to-File)
    
    For pre-transcoding your library overnight.
    """
    print("\n" + "="*60)
    print("Example 5: Batch Transcoding")
    print("="*60)
    
    client = GhostStreamClient(manual_server="192.168.4.2:8765")
    
    # High quality two-pass encoding
    job = client.transcode(
        source="http://192.168.4.1:5000/media/raw-video.mkv",
        mode="batch",
        format="mp4",
        resolution="1080p",
        video_codec="h264",
        two_pass=False  # Set True for best quality (2x slower)
    )
    
    if job.status != TranscodeStatus.ERROR:
        print(f"✅ Batch job started: {job.job_id}")
        print(f"   Polling for completion...")
        
        # Poll for completion (batch jobs take longer)
        result = client.wait_for_ready(job.job_id, timeout=3600)
        
        if result and result.status == TranscodeStatus.READY:
            print(f"✅ Transcoding complete!")
            print(f"   Download: {result.download_url}")
        else:
            print(f"❌ Batch transcoding failed")
        
        client.delete_job(job.job_id)
    else:
        print(f"❌ Failed: {job.error_message}")


def example_cleanup_management():
    """
    Example 6: Cleanup and Resource Management
    
    Monitor and manage temp files.
    Note: The SDK focuses on job management. For cleanup stats, 
    use direct API calls or the web dashboard.
    """
    print("\n" + "="*60)
    print("Example 6: Cleanup Management")
    print("="*60)
    
    client = GhostStreamClient(manual_server="192.168.4.2:8765")
    
    # Check server health
    if client.health_check():
        print("✅ Server is healthy")
        
        # Get capabilities to show server info
        caps = client.get_capabilities()
        if caps:
            print(f"   Version: {caps.get('version', 'unknown')}")
            print(f"   Platform: {caps.get('platform', 'unknown')}")
    else:
        print("❌ Server not reachable")
    
    print("\n💡 Tip: Jobs are automatically cleaned up after timeout.")
    print("   Use client.delete_job(job_id) to clean up immediately.")


def example_full_workflow():
    """
    Example 7: Full GhostHub Workflow
    
    Complete integration example showing the typical GhostHub flow.
    """
    print("\n" + "="*60)
    print("Example 7: Full GhostHub Workflow")
    print("="*60)
    
    client = GhostStreamClient(manual_server="192.168.4.2:8765")
    
    # 1. Check if transcoding is available
    if not client.health_check():
        print("GhostStream not available - play original file")
        return
    
    # 2. Get capabilities to show in UI
    caps = client.get_capabilities()
    has_gpu = any("nvenc" in str(caps.get("hw_accels", [])).lower() 
                  or "qsv" in str(caps.get("hw_accels", [])).lower()) if caps else False
    print(f"✅ GhostStream available (GPU: {has_gpu})")
    
    # 3. User clicks play on a video that needs transcoding
    video_url = "http://192.168.4.1:5000/media/hevc-movie.mkv"
    
    # 4. Decide: ABR for variable bandwidth, stream for simplicity
    use_abr = True  # Could be a user setting
    
    if use_abr:
        job = client.transcode(source=video_url, mode="abr")
    else:
        job = client.transcode(source=video_url, mode="stream", resolution="1080p")
    
    if job.status == TranscodeStatus.ERROR:
        print(f"❌ Transcoding failed - fall back to direct play: {job.error_message}")
        return
    
    print(f"✅ Transcoding started: {job.job_id}")
    
    # 5. Wait for stream to be ready (with timeout)
    result = client.wait_for_ready(job.job_id, timeout=30)
    
    if not result or result.status == TranscodeStatus.ERROR:
        print("❌ Transcoding timeout/error - fall back to direct play")
        client.cancel_job(job.job_id)
        return
    
    # 6. Give stream URL to video player
    stream_url = result.stream_url
    print(f"✅ Playing: {stream_url}")
    print(f"   HW Accel: {result.hw_accel_used or 'unknown'}")
    
    # 7. Simulate playback (in real app, player fetches HLS segments)
    print("\n   [Simulating playback for 5 seconds...]")
    import time
    time.sleep(5)
    
    # 8. User stops playback -> delete job to free resources
    print("\n   User stopped playback")
    client.delete_job(job.job_id)
    print("✅ Job cleaned up")


def example_progress_polling():
    """
    Example 8: Progress Tracking

    For OSS release, the Python SDK should use polling via get_job_status().
    """
    print("\n" + "="*60)
    print("Example 8: Progress Tracking (Polling)")
    print("="*60)

    client = GhostStreamClient(manual_server="192.168.4.2:8765")

    if not client.health_check():
        print("❌ GhostStream not reachable")
        return

    job = client.transcode(
        source="http://192.168.4.1:5000/media/video.mkv",
        mode="stream",
        resolution="720p"
    )

    if job.status == TranscodeStatus.ERROR:
        print(f"❌ Failed to start job: {job.error_message}")
        return

    print(f"✅ Job started: {job.job_id}")
    print("   Polling status every second...")

    try:
        while True:
            status = client.get_job_status(job.job_id)
            if not status:
                print("❌ Lost track of job state")
                break

            print(
                f"   📊 {status.progress:.1f}% | "
                f"{status.status.value.upper()} | "
                f"HW: {status.hw_accel_used or 'pending'}"
            )

            if status.status in (
                TranscodeStatus.READY,
                TranscodeStatus.ERROR,
                TranscodeStatus.CANCELLED,
            ):
                if status.stream_url:
                    print(f"   ▶ Stream: {status.stream_url}")
                if status.error_message:
                    print(f"   ❌ Error: {status.error_message}")
                break

            import time
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n   Interrupted")
    finally:
        client.delete_job(job.job_id)
        print("✅ Cleaned up")


if __name__ == "__main__":
    print("=" * 60)
    print("GhostStream Integration Examples")
    print("=" * 60)
    print("""
Available examples:
  1. Basic HLS Streaming
  2. Adaptive Bitrate (ABR) Streaming  
  3. HDR to SDR Tone Mapping
  4. Seeking/Resume Playback
  5. Batch Transcoding
  6. Cleanup Management
  7. Full GhostHub Workflow
  8. Progress Tracking (Polling)

Communication Methods:
  - HTTP REST: Start jobs, get status, cancel
  - Python SDK: Poll `get_job_status()` for progress
  - Browser/JS clients: `/ws/progress` for push updates
  - mDNS/UDP: Auto-discover servers on LAN

Make sure GhostStream is running on your PC first:
  python -m ghoststream

Edit the server URL in the examples to match your setup.
""")
    
    # Run the full workflow example
    example_full_workflow()

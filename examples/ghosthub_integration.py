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
    - Real-time WebSocket progress updates with job subscriptions

Communication Methods:
    - HTTP REST: API calls (start job, get status, cancel)
    - WebSocket: Real-time push updates (progress, status changes)
    - mDNS/UDP: Server discovery on LAN

SDK Installation:
    pip install ghoststream
    # or from source: pip install -e .
"""

import asyncio
import logging
import threading
import json
from typing import Optional, Dict, Any, Callable, List

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

# Optional WebSocket support
try:
    import websockets
    HAS_WEBSOCKETS = True
except ImportError:
    HAS_WEBSOCKETS = False
    logger.info("websockets not installed - using HTTP polling for progress")


# ============== WebSocket Client for Real-Time Updates ==============

class GhostStreamWSClient:
    """
    WebSocket client for real-time GhostStream progress updates.
    
    Benefits over HTTP polling:
    - Instant updates (no polling delay)
    - Lower server load
    - Job subscription filtering (only get updates you need)
    - Automatic reconnection
    
    Usage:
        ws_client = GhostStreamWSClient("192.168.4.2:8765")
        ws_client.on_progress = lambda job_id, data: print(f"{job_id}: {data['progress']}%")
        ws_client.connect()
        ws_client.subscribe_job("your-job-id")
    """
    
    def __init__(self, server_url: str):
        self.server_url = server_url
        self.ws_url = f"ws://{server_url}/ws/progress"
        self._ws = None
        self._loop = None
        self._thread = None
        self._running = False
        self._subscribed_jobs: set = set()
        
        # Callbacks
        self.on_progress: Optional[Callable[[str, Dict], None]] = None
        self.on_status_change: Optional[Callable[[str, str], None]] = None
        self.on_connect: Optional[Callable[[], None]] = None
        self.on_disconnect: Optional[Callable[[], None]] = None
    
    def connect(self) -> bool:
        """Connect to GhostStream WebSocket (runs in background thread)."""
        if not HAS_WEBSOCKETS:
            logger.warning("websockets package not installed")
            return False
        
        if self._running:
            return True
        
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info(f"WebSocket connecting to {self.ws_url}")
        return True
    
    def disconnect(self):
        """Disconnect from WebSocket."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
        self._thread = None
        logger.info("WebSocket disconnected")
    
    def subscribe_job(self, job_id: str):
        """Subscribe to updates for a specific job."""
        self._subscribed_jobs.add(job_id)
        if self._loop and self._ws:
            asyncio.run_coroutine_threadsafe(
                self._send({"type": "subscribe", "job_ids": [job_id]}),
                self._loop
            )
    
    def unsubscribe_job(self, job_id: str):
        """Unsubscribe from a job's updates."""
        self._subscribed_jobs.discard(job_id)
        if self._loop and self._ws:
            asyncio.run_coroutine_threadsafe(
                self._send({"type": "unsubscribe", "job_ids": [job_id]}),
                self._loop
            )
    
    def _run_loop(self):
        """Background thread event loop."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._connection_loop())
        self._loop.close()
    
    async def _connection_loop(self):
        """Main connection loop with auto-reconnect."""
        reconnect_delay = 1.0
        
        while self._running:
            try:
                async with websockets.connect(self.ws_url, ping_interval=None) as ws:
                    self._ws = ws
                    reconnect_delay = 1.0
                    logger.info("WebSocket connected")
                    
                    if self.on_connect:
                        self.on_connect()
                    
                    # Re-subscribe to tracked jobs
                    if self._subscribed_jobs:
                        await self._send({"type": "subscribe", "job_ids": list(self._subscribed_jobs)})
                    
                    # Message loop
                    async for message in ws:
                        if not self._running:
                            break
                        await self._handle_message(message)
                        
            except Exception as e:
                logger.warning(f"WebSocket error: {e}")
                self._ws = None
                
                if self.on_disconnect:
                    self.on_disconnect()
            
            # Reconnect with backoff
            if self._running:
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 30.0)
    
    async def _handle_message(self, message: str):
        """Handle incoming WebSocket message."""
        try:
            data = json.loads(message)
            msg_type = data.get("type", "")
            
            if msg_type == "ping":
                await self._send({"type": "pong"})
                
            elif msg_type == "progress":
                job_id = data.get("job_id")
                if self.on_progress:
                    self.on_progress(job_id, data.get("data", {}))
                    
            elif msg_type == "status_change":
                job_id = data.get("job_id")
                status = data.get("data", {}).get("status")
                if self.on_status_change:
                    self.on_status_change(job_id, status)
                # Auto-unsubscribe from finished jobs
                if status in ("ready", "error", "cancelled"):
                    self._subscribed_jobs.discard(job_id)
                    
        except json.JSONDecodeError:
            pass
    
    async def _send(self, data: dict):
        """Send message to WebSocket."""
        if self._ws:
            try:
                await self._ws.send(json.dumps(data))
            except:
                pass


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
    
    if not client.health_check_sync():
        print("❌ GhostStream not reachable")
        return
    
    print("✅ GhostStream is online")
    
    # Start transcoding using SDK
    job = client.transcode_sync(
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
    result = client.wait_for_ready_sync(job.job_id, timeout=30)
    
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
    client.delete_job_sync(job.job_id)
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
    
    if not client.health_check_sync():
        print("❌ GhostStream not reachable")
        return
    
    # Check capabilities using SDK
    caps = client.get_capabilities_sync()
    if caps:
        print(f"✅ Server capabilities:")
        print(f"   Video codecs: {caps.get('video_codecs', [])}")
        print(f"   HW acceleration: {caps.get('hw_accels', [])}")
    
    # Start ABR transcoding (multiple quality variants)
    job = client.transcode_sync(
        source="http://192.168.4.1:5000/media/4k-hdr-movie.mkv",
        mode="abr"
    )
    
    if job.status == TranscodeStatus.ERROR:
        print(f"❌ Failed to start ABR transcoding: {job.error_message}")
        return
    
    print(f"✅ ABR job started: {job.job_id}")
    
    # Wait for stream
    result = client.wait_for_ready_sync(job.job_id, timeout=60)
    
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
    client.delete_job_sync(job.job_id)


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
    job = client.transcode_sync(
        source="http://192.168.4.1:5000/media/hdr-demo.mkv",
        mode="stream",
        resolution="1080p",
        video_codec="h264",  # H264 doesn't support HDR, so tone mapping is applied
        tone_map=True
    )
    
    if job.status != TranscodeStatus.ERROR:
        print(f"✅ Job started with automatic HDR detection")
        print(f"   Tone mapping will be applied if source is HDR")
        
        result = client.wait_for_ready_sync(job.job_id, timeout=60)
        if result:
            print(f"   Result: {result.status.value}")
            if result.stream_url:
                print(f"   Stream: {result.stream_url}")
        
        client.delete_job_sync(job.job_id)
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
    job = client.transcode_sync(
        source="http://192.168.4.1:5000/media/movie.mkv",
        mode="stream",
        resolution="720p",
        start_time=1800  # 30 minutes in seconds
    )
    
    if job.status != TranscodeStatus.ERROR:
        print(f"✅ Started transcoding from 30:00")
        result = client.wait_for_ready_sync(job.job_id)
        if result and result.stream_url:
            print(f"   Stream: {result.stream_url}")
        client.delete_job_sync(job.job_id)
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
    job = client.transcode_sync(
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
        result = client.wait_for_ready_sync(job.job_id, timeout=3600)
        
        if result and result.status == TranscodeStatus.READY:
            print(f"✅ Transcoding complete!")
            print(f"   Download: {result.download_url}")
        else:
            print(f"❌ Batch transcoding failed")
        
        client.delete_job_sync(job.job_id)
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
    if client.health_check_sync():
        print("✅ Server is healthy")
        
        # Get capabilities to show server info
        caps = client.get_capabilities_sync()
        if caps:
            print(f"   Version: {caps.get('version', 'unknown')}")
            print(f"   Platform: {caps.get('platform', 'unknown')}")
    else:
        print("❌ Server not reachable")
    
    print("\n💡 Tip: Jobs are automatically cleaned up after timeout.")
    print("   Use client.delete_job_sync(job_id) to clean up immediately.")


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
    if not client.health_check_sync():
        print("GhostStream not available - play original file")
        return
    
    # 2. Get capabilities to show in UI
    caps = client.get_capabilities_sync()
    has_gpu = any("nvenc" in str(caps.get("hw_accels", [])).lower() 
                  or "qsv" in str(caps.get("hw_accels", [])).lower()) if caps else False
    print(f"✅ GhostStream available (GPU: {has_gpu})")
    
    # 3. User clicks play on a video that needs transcoding
    video_url = "http://192.168.4.1:5000/media/hevc-movie.mkv"
    
    # 4. Decide: ABR for variable bandwidth, stream for simplicity
    use_abr = True  # Could be a user setting
    
    if use_abr:
        job = client.transcode_sync(source=video_url, mode="abr")
    else:
        job = client.transcode_sync(source=video_url, mode="stream", resolution="1080p")
    
    if job.status == TranscodeStatus.ERROR:
        print(f"❌ Transcoding failed - fall back to direct play: {job.error_message}")
        return
    
    print(f"✅ Transcoding started: {job.job_id}")
    
    # 5. Wait for stream to be ready (with timeout)
    result = client.wait_for_ready_sync(job.job_id, timeout=30)
    
    if not result or result.status == TranscodeStatus.ERROR:
        print("❌ Transcoding timeout/error - fall back to direct play")
        client.cancel_job_sync(job.job_id)
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
    client.delete_job_sync(job.job_id)
    print("✅ Job cleaned up")


def example_websocket_progress():
    """
    Example 8: Real-Time WebSocket Progress
    
    Use WebSocket instead of polling for instant progress updates.
    Note: The SDK client has built-in WebSocket support via subscribe_progress().
    """
    print("\n" + "="*60)
    print("Example 8: Real-Time WebSocket Progress")
    print("="*60)
    
    if not HAS_WEBSOCKETS:
        print("❌ websockets package not installed")
        print("   Install with: pip install websockets")
        return
    
    # SDK client for API calls
    client = GhostStreamClient(manual_server="192.168.4.2:8765")
    
    if not client.health_check_sync():
        print("❌ GhostStream not reachable")
        return
    
    # WebSocket client for real-time updates (using the example WSClient above)
    ws_client = GhostStreamWSClient("192.168.4.2:8765")
    
    # Set up callbacks
    def on_progress(job_id: str, data: Dict):
        progress = data.get('progress', 0)
        fps = data.get('fps', 0)
        speed = data.get('speed', 0)
        print(f"   📊 {progress:.1f}% | {fps:.0f} fps | {speed:.1f}x speed")
    
    def on_status(job_id: str, status: str):
        print(f"   📌 Status changed: {status}")
    
    ws_client.on_progress = on_progress
    ws_client.on_status_change = on_status
    
    # Connect WebSocket
    ws_client.connect()
    import time
    time.sleep(1)  # Wait for connection
    
    print("✅ WebSocket connected")
    
    # Start a job using SDK
    job = client.transcode_sync(
        source="http://192.168.4.1:5000/media/video.mkv",
        mode="stream",
        resolution="720p"
    )
    
    if job.status == TranscodeStatus.ERROR:
        print(f"❌ Failed to start job: {job.error_message}")
        ws_client.disconnect()
        return
    
    print(f"✅ Job started: {job.job_id}")
    
    # Subscribe to this job's updates only
    ws_client.subscribe_job(job.job_id)
    print("✅ Subscribed to job updates via WebSocket")
    print("\n   Watching progress (Ctrl+C to stop)...")
    
    try:
        # Wait for completion (progress comes via WebSocket callback)
        while True:
            result = client.get_job_status_sync(job.job_id)
            if result and result.status in (TranscodeStatus.READY, TranscodeStatus.ERROR, TranscodeStatus.CANCELLED):
                print(f"\n✅ Job finished: {result.status.value}")
                if result.stream_url:
                    print(f"   Stream: {result.stream_url}")
                break
            time.sleep(2)
    except KeyboardInterrupt:
        print("\n   Interrupted")
    
    # Cleanup
    ws_client.disconnect()
    client.delete_job_sync(job.job_id)
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
  8. Real-Time WebSocket Progress (NEW)

Communication Methods:
  - HTTP REST: Start jobs, get status, cancel
  - WebSocket: Real-time progress push (recommended)
  - mDNS/UDP: Auto-discover servers on LAN

Make sure GhostStream is running on your PC first:
  python -m ghoststream

Edit the server URL in the examples to match your setup.
""")
    
    # Run the full workflow example
    example_full_workflow()

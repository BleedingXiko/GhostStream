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
"""

import asyncio
import logging
import httpx
import threading
import json
from typing import Optional, Dict, Any, Callable, List

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Optional WebSocket support
try:
    import websockets
    HAS_WEBSOCKETS = True
except ImportError:
    HAS_WEBSOCKETS = False
    logger.info("websockets not installed - using HTTP polling for progress")


# ============== Direct HTTP Client (Recommended for GhostHub) ==============

class GhostStreamClient:
    """
    Simple HTTP client for GhostStream.
    
    This is a synchronous client suitable for Flask/GhostHub integration.
    For the full async client, see ghoststream.client module.
    """
    
    def __init__(self, server_url: str = "http://192.168.4.2:8765"):
        self.server_url = server_url.rstrip("/")
        self.active_jobs: Dict[str, str] = {}  # video_id -> job_id
    
    def health_check(self) -> bool:
        """Check if GhostStream is reachable."""
        try:
            with httpx.Client(timeout=5.0) as http:
                resp = http.get(f"{self.server_url}/api/health")
                return resp.status_code == 200
        except:
            return False
    
    def get_capabilities(self) -> Optional[Dict]:
        """Get server capabilities (codecs, hw acceleration, etc.)."""
        try:
            with httpx.Client(timeout=10.0) as http:
                resp = http.get(f"{self.server_url}/api/capabilities")
                if resp.status_code == 200:
                    return resp.json()
        except Exception as e:
            logger.error(f"Failed to get capabilities: {e}")
        return None
    
    # ==================== STREAMING MODES ====================
    
    def start_stream(
        self,
        source: str,
        resolution: str = "1080p",
        start_time: float = 0,
        video_codec: str = "h264",
        hw_accel: str = "auto"
    ) -> Optional[Dict]:
        """
        Start single-quality HLS streaming (fastest startup).
        
        Best for: Quick playback, lower-powered clients, known bandwidth.
        """
        return self._transcode(
            source=source,
            mode="stream",
            resolution=resolution,
            start_time=start_time,
            video_codec=video_codec,
            hw_accel=hw_accel
        )
    
    def start_abr_stream(
        self,
        source: str,
        start_time: float = 0,
        video_codec: str = "h264",
        hw_accel: str = "auto"
    ) -> Optional[Dict]:
        """
        Start Adaptive Bitrate (ABR) streaming with multiple quality variants.
        
        Best for: Variable network conditions, quality selection UI.
        
        Automatically generates quality variants based on source:
        - 4K source -> 4K, 1080p, 720p, 480p variants
        - 1080p source -> 1080p, 720p, 480p variants
        - etc. (never upscales)
        """
        return self._transcode(
            source=source,
            mode="abr",
            resolution="original",  # ABR handles resolution automatically
            start_time=start_time,
            video_codec=video_codec,
            hw_accel=hw_accel
        )
    
    def start_batch_transcode(
        self,
        source: str,
        output_format: str = "mp4",
        resolution: str = "1080p",
        video_codec: str = "h264",
        two_pass: bool = False
    ) -> Optional[Dict]:
        """
        Start batch (file-to-file) transcoding.
        
        Best for: Pre-transcoding library, overnight processing.
        
        Args:
            two_pass: Enable two-pass encoding for better quality (slower)
        """
        return self._transcode(
            source=source,
            mode="batch",
            format=output_format,
            resolution=resolution,
            video_codec=video_codec,
            two_pass=two_pass
        )
    
    def _transcode(
        self,
        source: str,
        mode: str = "stream",
        format: str = "hls",
        resolution: str = "1080p",
        video_codec: str = "h264",
        audio_codec: str = "aac",
        bitrate: str = "auto",
        hw_accel: str = "auto",
        start_time: float = 0,
        tone_map: bool = True,
        two_pass: bool = False
    ) -> Optional[Dict]:
        """Internal transcode method."""
        request_body = {
            "source": source,
            "mode": mode,
            "output": {
                "format": format,
                "video_codec": video_codec,
                "audio_codec": audio_codec,
                "resolution": resolution,
                "bitrate": bitrate,
                "hw_accel": hw_accel,
                "tone_map": tone_map,
                "two_pass": two_pass
            },
            "start_time": start_time
        }
        
        try:
            with httpx.Client(timeout=30.0) as http:
                resp = http.post(
                    f"{self.server_url}/api/transcode/start",
                    json=request_body
                )
                if resp.status_code == 200:
                    return resp.json()
                else:
                    logger.error(f"Transcode error: {resp.text[:200]}")
        except Exception as e:
            logger.error(f"Transcode request failed: {e}")
        return None
    
    # ==================== JOB MANAGEMENT ====================
    
    def get_job_status(self, job_id: str) -> Optional[Dict]:
        """Get status of a transcoding job."""
        try:
            with httpx.Client(timeout=10.0) as http:
                resp = http.get(f"{self.server_url}/api/transcode/{job_id}/status")
                if resp.status_code == 200:
                    return resp.json()
        except:
            pass
        return None
    
    def cancel_job(self, job_id: str) -> bool:
        """Cancel a running job."""
        try:
            with httpx.Client(timeout=10.0) as http:
                resp = http.post(f"{self.server_url}/api/transcode/{job_id}/cancel")
                return resp.status_code == 200
        except:
            return False
    
    def delete_job(self, job_id: str) -> bool:
        """Delete a job and clean up its temp files."""
        try:
            with httpx.Client(timeout=10.0) as http:
                resp = http.delete(f"{self.server_url}/api/transcode/{job_id}")
                return resp.status_code == 200
        except:
            return False
    
    def wait_for_ready(self, job_id: str, timeout: float = 60) -> Optional[Dict]:
        """Wait for a job to be ready."""
        import time
        start = time.time()
        while time.time() - start < timeout:
            status = self.get_job_status(job_id)
            if status and status.get("status") in ("ready", "error", "cancelled"):
                return status
            time.sleep(1)
        return None
    
    # ==================== CLEANUP ====================
    
    def get_cleanup_stats(self) -> Optional[Dict]:
        """Get cleanup statistics (active jobs, temp space, etc.)."""
        try:
            with httpx.Client(timeout=10.0) as http:
                resp = http.get(f"{self.server_url}/api/cleanup/stats")
                if resp.status_code == 200:
                    return resp.json()
        except:
            pass
        return None
    
    def run_cleanup(self) -> Optional[Dict]:
        """Manually trigger cleanup of stale jobs and orphaned files."""
        try:
            with httpx.Client(timeout=30.0) as http:
                resp = http.post(f"{self.server_url}/api/cleanup/run")
                if resp.status_code == 200:
                    return resp.json()
        except:
            pass
        return None


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
    
    client = GhostStreamClient("http://192.168.4.2:8765")
    
    if not client.health_check():
        print("❌ GhostStream not reachable")
        return
    
    print("✅ GhostStream is online")
    
    # Start transcoding
    job = client.start_stream(
        source="http://192.168.4.1:5000/media/movie.mkv",
        resolution="1080p"
    )
    
    if not job:
        print("❌ Failed to start transcoding")
        return
    
    print(f"✅ Job started: {job['job_id']}")
    print(f"   HW Accel: {job.get('hw_accel_used', 'pending')}")
    
    # Wait for stream to be ready
    status = client.wait_for_ready(job["job_id"], timeout=30)
    
    if status and status.get("status") == "ready":
        print(f"✅ Stream ready!")
        print(f"   URL: {status['stream_url']}")
        print(f"\n   Play with: ffplay '{status['stream_url']}'")
    else:
        print(f"❌ Transcoding failed: {status}")
    
    # Cleanup when done
    input("\nPress Enter to cleanup...")
    client.delete_job(job["job_id"])
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
    
    client = GhostStreamClient("http://192.168.4.2:8765")
    
    if not client.health_check():
        print("❌ GhostStream not reachable")
        return
    
    # Check capabilities
    caps = client.get_capabilities()
    if caps:
        print(f"✅ Server capabilities:")
        print(f"   Video codecs: {caps.get('video_codecs', [])}")
        print(f"   HW acceleration: {caps.get('hw_accels', [])}")
    
    # Start ABR transcoding (multiple quality variants)
    job = client.start_abr_stream(
        source="http://192.168.4.1:5000/media/4k-hdr-movie.mkv"
    )
    
    if not job:
        print("❌ Failed to start ABR transcoding")
        return
    
    print(f"✅ ABR job started: {job['job_id']}")
    
    # Wait for stream
    status = client.wait_for_ready(job["job_id"], timeout=60)
    
    if status and status.get("status") == "ready":
        print(f"✅ ABR stream ready!")
        print(f"   Master playlist: {status['stream_url']}")
        print(f"\n   The master.m3u8 contains multiple quality variants.")
        print(f"   HLS players automatically select best quality.")
    else:
        print(f"❌ ABR transcoding failed: {status}")
    
    # Cleanup
    input("\nPress Enter to cleanup...")
    client.delete_job(job["job_id"])


def example_hdr_content():
    """
    Example 3: HDR Content Handling
    
    GhostStream automatically tone-maps HDR to SDR for compatibility.
    """
    print("\n" + "="*60)
    print("Example 3: HDR to SDR Tone Mapping")
    print("="*60)
    
    client = GhostStreamClient("http://192.168.4.2:8765")
    
    # HDR content (10-bit HEVC with HDR10/Dolby Vision)
    # GhostStream detects this and applies tone mapping automatically
    job = client.start_stream(
        source="http://192.168.4.1:5000/media/hdr-demo.mkv",
        resolution="1080p",
        video_codec="h264"  # H264 doesn't support HDR, so tone mapping is applied
    )
    
    if job:
        print(f"✅ Job started with automatic HDR detection")
        print(f"   Tone mapping will be applied if source is HDR")
        
        status = client.wait_for_ready(job["job_id"], timeout=60)
        if status:
            print(f"   Result: {status.get('status')}")
            if status.get("stream_url"):
                print(f"   Stream: {status['stream_url']}")
        
        client.delete_job(job["job_id"])


def example_seeking():
    """
    Example 4: Seeking/Resume Playback
    
    Start transcoding from a specific position.
    """
    print("\n" + "="*60)
    print("Example 4: Seeking (Start from 30 minutes)")
    print("="*60)
    
    client = GhostStreamClient("http://192.168.4.2:8765")
    
    # Start from 30 minutes (1800 seconds)
    job = client.start_stream(
        source="http://192.168.4.1:5000/media/movie.mkv",
        resolution="720p",
        start_time=1800  # 30 minutes in seconds
    )
    
    if job:
        print(f"✅ Started transcoding from 30:00")
        status = client.wait_for_ready(job["job_id"])
        if status and status.get("stream_url"):
            print(f"   Stream: {status['stream_url']}")
        client.delete_job(job["job_id"])


def example_batch_transcode():
    """
    Example 5: Batch Transcoding (File-to-File)
    
    For pre-transcoding your library overnight.
    """
    print("\n" + "="*60)
    print("Example 5: Batch Transcoding")
    print("="*60)
    
    client = GhostStreamClient("http://192.168.4.2:8765")
    
    # High quality two-pass encoding
    job = client.start_batch_transcode(
        source="http://192.168.4.1:5000/media/raw-video.mkv",
        output_format="mp4",
        resolution="1080p",
        video_codec="h264",
        two_pass=False  # Set True for best quality (2x slower)
    )
    
    if job:
        print(f"✅ Batch job started: {job['job_id']}")
        print(f"   Polling for completion...")
        
        # Poll for completion (batch jobs take longer)
        status = client.wait_for_ready(job["job_id"], timeout=3600)
        
        if status and status.get("status") == "ready":
            print(f"✅ Transcoding complete!")
            print(f"   Download: {status.get('download_url')}")
        else:
            print(f"❌ Batch transcoding failed")
        
        client.delete_job(job["job_id"])


def example_cleanup_management():
    """
    Example 6: Cleanup and Resource Management
    
    Monitor and manage temp files.
    """
    print("\n" + "="*60)
    print("Example 6: Cleanup Management")
    print("="*60)
    
    client = GhostStreamClient("http://192.168.4.2:8765")
    
    # Check current cleanup stats
    stats = client.get_cleanup_stats()
    if stats:
        print(f"📊 Cleanup Stats:")
        print(f"   Total jobs: {stats.get('total_jobs', 0)}")
        print(f"   Active jobs: {stats.get('active_jobs', 0)}")
        print(f"   Ready jobs: {stats.get('ready_jobs', 0)}")
        print(f"   Cleaned jobs: {stats.get('cleaned_jobs', 0)}")
        print(f"   Nearly stale: {stats.get('nearly_stale', 0)}")
        print(f"   Temp dir: {stats.get('temp_dir')}")
    
    # Manual cleanup (optional - runs automatically every 5 min)
    print("\n🧹 Running manual cleanup...")
    result = client.run_cleanup()
    if result:
        print(f"   Stale jobs cleaned: {result.get('stale_jobs_cleaned', 0)}")
        print(f"   Orphaned dirs cleaned: {result.get('orphaned_dirs_cleaned', 0)}")


def example_full_workflow():
    """
    Example 7: Full GhostHub Workflow
    
    Complete integration example showing the typical GhostHub flow.
    """
    print("\n" + "="*60)
    print("Example 7: Full GhostHub Workflow")
    print("="*60)
    
    client = GhostStreamClient("http://192.168.4.2:8765")
    
    # 1. Check if transcoding is available
    if not client.health_check():
        print("GhostStream not available - play original file")
        return
    
    # 2. Get capabilities to show in UI
    caps = client.get_capabilities()
    has_gpu = any("nvenc" in str(caps.get("hw_accels", [])).lower() 
                  or "qsv" in str(caps.get("hw_accels", [])).lower())
    print(f"✅ GhostStream available (GPU: {has_gpu})")
    
    # 3. User clicks play on a video that needs transcoding
    video_url = "http://192.168.4.1:5000/media/hevc-movie.mkv"
    
    # 4. Decide: ABR for variable bandwidth, stream for simplicity
    use_abr = True  # Could be a user setting
    
    if use_abr:
        job = client.start_abr_stream(source=video_url)
    else:
        job = client.start_stream(source=video_url, resolution="1080p")
    
    if not job:
        print("❌ Transcoding failed - fall back to direct play")
        return
    
    job_id = job["job_id"]
    print(f"✅ Transcoding started: {job_id}")
    
    # 5. Wait for stream to be ready (with timeout)
    status = client.wait_for_ready(job_id, timeout=30)
    
    if not status or status.get("status") != "ready":
        print("❌ Transcoding timeout - fall back to direct play")
        client.cancel_job(job_id)
        return
    
    # 6. Give stream URL to video player
    stream_url = status["stream_url"]
    print(f"✅ Playing: {stream_url}")
    print(f"   HW Accel: {status.get('hw_accel_used', 'unknown')}")
    
    # 7. Simulate playback (in real app, player fetches HLS segments)
    print("\n   [Simulating playback for 5 seconds...]")
    import time
    time.sleep(5)
    
    # 8. User stops playback -> delete job to free resources
    print("\n   User stopped playback")
    client.delete_job(job_id)
    print("✅ Job cleaned up")


def example_websocket_progress():
    """
    Example 8: Real-Time WebSocket Progress
    
    Use WebSocket instead of polling for instant progress updates.
    """
    print("\n" + "="*60)
    print("Example 8: Real-Time WebSocket Progress")
    print("="*60)
    
    if not HAS_WEBSOCKETS:
        print("❌ websockets package not installed")
        print("   Install with: pip install websockets")
        return
    
    # HTTP client for API calls
    http_client = GhostStreamClient("http://192.168.4.2:8765")
    
    if not http_client.health_check():
        print("❌ GhostStream not reachable")
        return
    
    # WebSocket client for real-time updates
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
    
    # Start a job
    job = http_client.start_stream(
        source="http://192.168.4.1:5000/media/video.mkv",
        resolution="720p"
    )
    
    if not job:
        print("❌ Failed to start job")
        ws_client.disconnect()
        return
    
    job_id = job["job_id"]
    print(f"✅ Job started: {job_id}")
    
    # Subscribe to this job's updates only
    ws_client.subscribe_job(job_id)
    print("✅ Subscribed to job updates via WebSocket")
    print("\n   Watching progress (Ctrl+C to stop)...")
    
    try:
        # Wait for completion (progress comes via WebSocket callback)
        while True:
            status = http_client.get_job_status(job_id)
            if status and status.get("status") in ("ready", "error", "cancelled"):
                print(f"\n✅ Job finished: {status.get('status')}")
                if status.get("stream_url"):
                    print(f"   Stream: {status['stream_url']}")
                break
            time.sleep(2)
    except KeyboardInterrupt:
        print("\n   Interrupted")
    
    # Cleanup
    ws_client.disconnect()
    http_client.delete_job(job_id)
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

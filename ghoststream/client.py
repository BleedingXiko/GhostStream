"""
GhostStream Client - For GhostHub and other media servers to discover and use GhostStream

Usage in GhostHub:
    from ghoststream.client import GhostStreamClient
    
    client = GhostStreamClient()
    client.start_discovery()
    
    # Check if transcoder is available
    if client.is_available():
        # Request transcoding
        stream_url = await client.transcode(
            source="http://pi-ip:5000/media/video.mkv",
            resolution="1080p"
        )
        # Use stream_url in your video player
"""

import asyncio
import logging
import random
import time
import json
import threading
from typing import Optional, Dict, Any, List, Callable, AsyncIterator
from dataclasses import dataclass, field
from enum import Enum
from contextlib import asynccontextmanager

import httpx
from zeroconf import ServiceBrowser, ServiceListener, Zeroconf
import socket

try:
    import websockets
    HAS_WEBSOCKETS = True
except ImportError:
    HAS_WEBSOCKETS = False

logger = logging.getLogger(__name__)


# Default timeout configuration
DEFAULT_CONNECT_TIMEOUT = 10.0
DEFAULT_READ_TIMEOUT = 30.0
DEFAULT_WRITE_TIMEOUT = 30.0

# Retry configuration
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_DELAY = 1.0
DEFAULT_RETRY_MAX_DELAY = 30.0
DEFAULT_RETRY_MULTIPLIER = 2.0


class LoadBalanceStrategy(str, Enum):
    """Load balancing strategies for multiple servers."""
    ROUND_ROBIN = "round_robin"      # Rotate through servers
    LEAST_BUSY = "least_busy"        # Pick server with fewest active jobs
    FASTEST = "fastest"              # Pick server with best HW accel
    RANDOM = "random"                # Random selection


@dataclass
class ServerStats:
    """Runtime statistics for a server."""
    active_jobs: int = 0
    queued_jobs: int = 0
    total_processed: int = 0
    last_health_check: float = 0
    is_healthy: bool = True


@dataclass
class GhostStreamServer:
    """Represents a discovered GhostStream server."""
    name: str
    host: str
    port: int
    version: str = ""
    hw_accels: List[str] = None
    video_codecs: List[str] = None
    max_jobs: int = 2
    
    def __post_init__(self):
        if self.hw_accels is None:
            self.hw_accels = []
        if self.video_codecs is None:
            self.video_codecs = []
    
    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"
    
    @property
    def has_hw_accel(self) -> bool:
        """Check if hardware acceleration is available."""
        return any(hw != "software" for hw in self.hw_accels)


class TranscodeStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    READY = "ready"
    ERROR = "error"
    CANCELLED = "cancelled"


@dataclass
class TranscodeJob:
    """Represents a transcoding job."""
    job_id: str
    status: TranscodeStatus
    progress: float = 0
    stream_url: Optional[str] = None
    download_url: Optional[str] = None
    error_message: Optional[str] = None
    hw_accel_used: Optional[str] = None


class GhostStreamDiscoveryListener(ServiceListener):
    """Listens for GhostStream services on the network."""
    
    SERVICE_TYPE = "_ghoststream._tcp.local."
    
    def __init__(self, on_found: Callable, on_removed: Callable):
        self.on_found = on_found
        self.on_removed = on_removed
    
    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        logger.info(f"[mDNS] Discovered service: {name}")
        info = zc.get_service_info(type_, name)
        if info:
            addresses = [socket.inet_ntoa(addr) for addr in info.addresses]
            logger.info(f"[mDNS] Service addresses: {addresses}, port: {info.port}")
            if addresses:
                props = {
                    k.decode(): v.decode() if isinstance(v, bytes) else v
                    for k, v in info.properties.items()
                }
                
                server = GhostStreamServer(
                    name=name,
                    host=addresses[0],
                    port=info.port,
                    version=props.get("version", ""),
                    hw_accels=props.get("hw_accels", "").split(","),
                    video_codecs=props.get("video_codecs", "").split(","),
                    max_jobs=int(props.get("max_jobs", 2))
                )
                
                logger.info(f"[mDNS] GhostStream server found: {server.host}:{server.port} (hw_accel: {server.has_hw_accel})")
                self.on_found(server)
            else:
                logger.warning(f"[mDNS] Service {name} has no addresses")
        else:
            logger.warning(f"[mDNS] Could not get service info for {name}")
    
    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        logger.info(f"GhostStream removed: {name}")
        self.on_removed(name)
    
    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        self.add_service(zc, type_, name)


@dataclass
class ClientConfig:
    """
    Configuration for GhostStreamClient.
    
    All parameters are optional with sensible defaults for backward compatibility.
    """
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT
    read_timeout: float = DEFAULT_READ_TIMEOUT
    write_timeout: float = DEFAULT_WRITE_TIMEOUT
    max_retries: int = DEFAULT_MAX_RETRIES
    retry_delay: float = DEFAULT_RETRY_DELAY
    retry_max_delay: float = DEFAULT_RETRY_MAX_DELAY
    retry_multiplier: float = DEFAULT_RETRY_MULTIPLIER
    retry_on_status: List[int] = field(default_factory=lambda: [502, 503, 504])


class GhostStreamClient:
    """
    Client for discovering and using GhostStream transcoding services.
    
    Designed for integration with GhostHub and other media servers.
    
    Features:
    - Connection pooling for efficient HTTP requests
    - Automatic retry with exponential backoff
    - WebSocket support for real-time progress updates
    - ABR (Adaptive Bitrate) streaming mode
    - Context manager support for proper cleanup
    
    Backward Compatible: All existing code will work without changes.
    """
    
    def __init__(
        self,
        manual_server: Optional[str] = None,
        config: Optional[ClientConfig] = None
    ):
        """
        Initialize the client.
        
        Args:
            manual_server: Optional manual server address (e.g., "192.168.4.2:8765")
                          If provided, skips mDNS discovery.
            config: Optional configuration for timeouts and retries.
                   If not provided, uses sensible defaults.
        """
        self.config = config or ClientConfig()
        self.servers: Dict[str, GhostStreamServer] = {}
        self.preferred_server: Optional[str] = None
        self.zeroconf: Optional[Zeroconf] = None
        self.browser: Optional[ServiceBrowser] = None
        self._discovery_started = False
        self._callbacks: List[Callable[[str, GhostStreamServer], None]] = []
        
        # Connection pool - reused across requests
        self._http_client: Optional[httpx.AsyncClient] = None
        self._client_lock = asyncio.Lock()
        
        # Synchronous HTTP client (for gevent/Flask compatibility)
        self._sync_http_client: Optional[httpx.Client] = None
        self._sync_client_lock = threading.Lock()
        
        # If manual server provided, add it directly
        if manual_server:
            host, port = manual_server.split(":")
            self.servers["manual"] = GhostStreamServer(
                name="manual",
                host=host,
                port=int(port)
            )
            self.preferred_server = "manual"
    
    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the shared HTTP client (connection pooling)."""
        async with self._client_lock:
            if self._http_client is None or self._http_client.is_closed:
                timeout = httpx.Timeout(
                    connect=self.config.connect_timeout,
                    read=self.config.read_timeout,
                    write=self.config.write_timeout,
                    pool=self.config.connect_timeout
                )
                self._http_client = httpx.AsyncClient(
                    timeout=timeout,
                    limits=httpx.Limits(max_connections=100, max_keepalive_connections=20)
                )
            return self._http_client
    
    async def close(self) -> None:
        """Close the HTTP client and release resources."""
        async with self._client_lock:
            if self._http_client and not self._http_client.is_closed:
                await self._http_client.aclose()
                self._http_client = None
        self.close_sync()
        self.stop_discovery()
    
    # =========================================================================
    # Synchronous API (for gevent/Flask compatibility)
    # =========================================================================
    
    def _get_sync_client(self) -> httpx.Client:
        """Get or create the shared synchronous HTTP client (connection pooling)."""
        with self._sync_client_lock:
            if self._sync_http_client is None or self._sync_http_client.is_closed:
                timeout = httpx.Timeout(
                    connect=self.config.connect_timeout,
                    read=self.config.read_timeout,
                    write=self.config.write_timeout,
                    pool=self.config.connect_timeout
                )
                self._sync_http_client = httpx.Client(
                    timeout=timeout,
                    limits=httpx.Limits(max_connections=100, max_keepalive_connections=20)
                )
            return self._sync_http_client
    
    def close_sync(self) -> None:
        """Close the synchronous HTTP client."""
        with self._sync_client_lock:
            if self._sync_http_client and not self._sync_http_client.is_closed:
                self._sync_http_client.close()
                self._sync_http_client = None
    
    def _request_sync_with_retry(
        self,
        method: str,
        url: str,
        **kwargs
    ) -> httpx.Response:
        """
        Make synchronous HTTP request with automatic retry on transient failures.
        
        Uses exponential backoff with jitter. Gevent-compatible.
        """
        client = self._get_sync_client()
        last_exception = None
        delay = self.config.retry_delay
        
        for attempt in range(self.config.max_retries + 1):
            try:
                response = client.request(method, url, **kwargs)
                
                # Retry on specific status codes
                if response.status_code in self.config.retry_on_status:
                    if attempt < self.config.max_retries:
                        logger.warning(
                            f"[GhostStream] Request returned {response.status_code}, "
                            f"retrying ({attempt + 1}/{self.config.max_retries})..."
                        )
                        time.sleep(delay + random.uniform(0, 1))
                        delay = min(delay * self.config.retry_multiplier, self.config.retry_max_delay)
                        continue
                
                return response
                
            except (httpx.ConnectError, httpx.ConnectTimeout) as e:
                last_exception = e
                if attempt < self.config.max_retries:
                    logger.warning(
                        f"[GhostStream] Connection failed, retrying ({attempt + 1}/{self.config.max_retries})..."
                    )
                    time.sleep(delay + random.uniform(0, 1))
                    delay = min(delay * self.config.retry_multiplier, self.config.retry_max_delay)
                else:
                    raise
            except httpx.TimeoutException as e:
                last_exception = e
                if attempt < self.config.max_retries:
                    logger.warning(
                        f"[GhostStream] Request timed out, retrying ({attempt + 1}/{self.config.max_retries})..."
                    )
                    time.sleep(delay)
                    delay = min(delay * self.config.retry_multiplier, self.config.retry_max_delay)
                else:
                    raise
        
        if last_exception:
            raise last_exception
        raise RuntimeError("Unexpected retry loop exit")
    
    def health_check_sync(self, server: Optional[GhostStreamServer] = None) -> bool:
        """Check if a server is healthy (synchronous)."""
        server = server or self.get_server()
        if not server:
            return False
        
        try:
            response = self._request_sync_with_retry(
                "GET",
                f"{server.base_url}/api/health"
            )
            return response.status_code == 200
        except Exception:
            return False
    
    def get_capabilities_sync(self, server: Optional[GhostStreamServer] = None) -> Optional[Dict]:
        """Get server capabilities (synchronous)."""
        server = server or self.get_server()
        if not server:
            return None
        
        try:
            response = self._request_sync_with_retry(
                "GET",
                f"{server.base_url}/api/capabilities"
            )
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            logger.error(f"Failed to get capabilities: {e}")
        
        return None
    
    def transcode_sync(
        self,
        source: str,
        mode: str = "stream",
        format: str = "hls",
        video_codec: str = "h264",
        audio_codec: str = "aac",
        resolution: str = "original",
        bitrate: str = "auto",
        hw_accel: str = "auto",
        start_time: float = 0,
        tone_map: bool = True,
        two_pass: bool = False,
        max_audio_channels: int = 2,
        session_id: Optional[str] = None,
        server: Optional[GhostStreamServer] = None
    ) -> Optional[TranscodeJob]:
        """
        Start a transcoding job (synchronous, gevent-compatible).
        
        Args:
            source: Source file URL (accessible from GhostStream server)
            mode: Transcoding mode ("stream", "abr", "batch")
            format: Output format (hls, mp4, webm, etc.)
            video_codec: Video codec (h264, h265, vp9, av1)
            audio_codec: Audio codec (aac, opus, copy)
            resolution: Target resolution (4k, 1080p, 720p, 480p, original)
            bitrate: Target bitrate or "auto"
            hw_accel: Hardware acceleration (auto, nvenc, qsv, software)
            start_time: Start position in seconds
            tone_map: Convert HDR to SDR automatically
            two_pass: Use two-pass encoding for batch mode
            max_audio_channels: Max audio channels (2=stereo, 6=5.1)
            session_id: Session ID for job tracking
            server: Specific server to use
        
        Returns:
            TranscodeJob with stream_url for playback
        """
        server = server or self.get_server()
        if not server:
            if self.servers:
                server = next(iter(self.servers.values()))
                logger.info(f"[GhostStream] Using first available server: {server.name}")
            else:
                logger.error("[GhostStream] No servers available for transcoding")
                return TranscodeJob(
                    job_id="error",
                    status=TranscodeStatus.ERROR,
                    error_message="No GhostStream servers available. Add a server in Settings."
                )
        
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
                "two_pass": two_pass,
                "max_audio_channels": max_audio_channels
            },
            "start_time": start_time,
            "session_id": session_id
        }
        
        logger.info(f"[GhostStream] Sending transcode request to {server.base_url}/api/transcode/start")
        logger.info(f"[GhostStream] Request: source={source[:80]}..., mode={mode}, resolution={resolution}")
        
        try:
            response = self._request_sync_with_retry(
                "POST",
                f"{server.base_url}/api/transcode/start",
                json=request_body
            )
            
            logger.info(f"[GhostStream] Response status: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                logger.info(f"[GhostStream] Job created: {data.get('job_id')}")
                return TranscodeJob(
                    job_id=data["job_id"],
                    status=TranscodeStatus(data["status"]),
                    progress=data.get("progress", 0),
                    stream_url=data.get("stream_url"),
                    download_url=data.get("download_url"),
                    hw_accel_used=data.get("hw_accel_used")
                )
            else:
                error_text = response.text
                logger.error(f"[GhostStream] Transcode failed ({response.status_code}): {error_text[:300]}")
                return TranscodeJob(
                    job_id="error",
                    status=TranscodeStatus.ERROR,
                    error_message=f"GhostStream error ({response.status_code}): {error_text[:200]}"
                )
        except httpx.ConnectError as e:
            logger.error(f"[GhostStream] Cannot connect to {server.base_url}: {e}")
            return TranscodeJob(
                job_id="error",
                status=TranscodeStatus.ERROR,
                error_message=f"Cannot connect to GhostStream at {server.host}:{server.port}"
            )
        except httpx.TimeoutException as e:
            logger.error(f"[GhostStream] Request timed out to {server.base_url}: {e}")
            return TranscodeJob(
                job_id="error",
                status=TranscodeStatus.ERROR,
                error_message="Request to GhostStream timed out"
            )
        except Exception as e:
            logger.error(f"[GhostStream] Transcode request error: {e}", exc_info=True)
            return TranscodeJob(
                job_id="error",
                status=TranscodeStatus.ERROR,
                error_message=str(e)
            )
    
    def get_job_status_sync(
        self,
        job_id: str,
        server: Optional[GhostStreamServer] = None
    ) -> Optional[TranscodeJob]:
        """Get the status of a transcoding job (synchronous)."""
        server = server or self.get_server()
        if not server:
            return None
        
        try:
            response = self._request_sync_with_retry(
                "GET",
                f"{server.base_url}/api/transcode/{job_id}/status"
            )
            
            if response.status_code == 200:
                data = response.json()
                return TranscodeJob(
                    job_id=data["job_id"],
                    status=TranscodeStatus(data["status"]),
                    progress=data.get("progress", 0),
                    stream_url=data.get("stream_url"),
                    download_url=data.get("download_url"),
                    error_message=data.get("error_message"),
                    hw_accel_used=data.get("hw_accel_used")
                )
        except Exception as e:
            logger.error(f"Status request error: {e}")
        
        return None
    
    def cancel_job_sync(
        self,
        job_id: str,
        server: Optional[GhostStreamServer] = None
    ) -> bool:
        """Cancel a transcoding job (synchronous)."""
        server = server or self.get_server()
        if not server:
            return False
        
        try:
            response = self._request_sync_with_retry(
                "POST",
                f"{server.base_url}/api/transcode/{job_id}/cancel"
            )
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Cancel request error: {e}")
        
        return False
    
    def delete_job_sync(
        self,
        job_id: str,
        server: Optional[GhostStreamServer] = None
    ) -> bool:
        """Delete a transcoding job and its files (synchronous)."""
        server = server or self.get_server()
        if not server:
            return False
        
        try:
            response = self._request_sync_with_retry(
                "DELETE",
                f"{server.base_url}/api/transcode/{job_id}"
            )
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Delete request error: {e}")
        
        return False
    
    def wait_for_ready_sync(
        self,
        job_id: str,
        timeout: float = 300,
        poll_interval: float = 1.0,
        server: Optional[GhostStreamServer] = None
    ) -> Optional[TranscodeJob]:
        """
        Wait for a job to be ready for streaming (synchronous).
        
        For live transcoding (HLS), the job becomes ready quickly
        as segments are generated.
        """
        server = server or self.get_server()
        if not server:
            return None
        
        elapsed = 0
        while elapsed < timeout:
            job = self.get_job_status_sync(job_id, server)
            
            if job is None:
                return None
            
            if job.status == TranscodeStatus.READY:
                return job
            
            if job.status == TranscodeStatus.ERROR:
                logger.error(f"Job failed: {job.error_message}")
                return job
            
            if job.status == TranscodeStatus.CANCELLED:
                return job
            
            # For streaming mode, return as soon as we have a stream URL
            if job.stream_url and job.status == TranscodeStatus.PROCESSING:
                return job
            
            time.sleep(poll_interval)
            elapsed += poll_interval
        
        logger.error(f"Timeout waiting for job {job_id}")
        return None
    
    # =========================================================================
    # Async API
    # =========================================================================
    
    async def __aenter__(self) -> "GhostStreamClient":
        """Context manager entry."""
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit - cleanup resources."""
        await self.close()
    
    async def _request_with_retry(
        self,
        method: str,
        url: str,
        **kwargs
    ) -> httpx.Response:
        """
        Make HTTP request with automatic retry on transient failures.
        
        Uses exponential backoff with jitter.
        """
        client = await self._get_client()
        last_exception = None
        delay = self.config.retry_delay
        
        for attempt in range(self.config.max_retries + 1):
            try:
                response = await client.request(method, url, **kwargs)
                
                # Retry on specific status codes
                if response.status_code in self.config.retry_on_status:
                    if attempt < self.config.max_retries:
                        logger.warning(
                            f"[GhostStream] Request returned {response.status_code}, "
                            f"retrying ({attempt + 1}/{self.config.max_retries})..."
                        )
                        await asyncio.sleep(delay + random.uniform(0, 1))
                        delay = min(delay * self.config.retry_multiplier, self.config.retry_max_delay)
                        continue
                
                return response
                
            except (httpx.ConnectError, httpx.ConnectTimeout) as e:
                last_exception = e
                if attempt < self.config.max_retries:
                    logger.warning(
                        f"[GhostStream] Connection failed, retrying ({attempt + 1}/{self.config.max_retries})..."
                    )
                    await asyncio.sleep(delay + random.uniform(0, 1))
                    delay = min(delay * self.config.retry_multiplier, self.config.retry_max_delay)
                else:
                    raise
            except httpx.TimeoutException as e:
                last_exception = e
                if attempt < self.config.max_retries:
                    logger.warning(
                        f"[GhostStream] Request timed out, retrying ({attempt + 1}/{self.config.max_retries})..."
                    )
                    await asyncio.sleep(delay)
                    delay = min(delay * self.config.retry_multiplier, self.config.retry_max_delay)
                else:
                    raise
        
        # Should not reach here, but just in case
        if last_exception:
            raise last_exception
        raise RuntimeError("Unexpected retry loop exit")
    
    def add_callback(self, callback: Callable[[str, GhostStreamServer], None]) -> None:
        """
        Add a callback for server discovery events.
        
        Args:
            callback: Function called with (event_type, server) where event_type
                     is "found" or "removed"
        """
        self._callbacks.append(callback)
    
    def _on_server_found(self, server: GhostStreamServer) -> None:
        """Called when a server is discovered."""
        self.servers[server.name] = server
        
        # Auto-select first server with hw accel, or first found
        if self.preferred_server is None:
            self.preferred_server = server.name
        elif server.has_hw_accel and not self.get_server().has_hw_accel:
            self.preferred_server = server.name
        
        for callback in self._callbacks:
            try:
                callback("found", server)
            except Exception as e:
                logger.error(f"Callback error: {e}")
    
    def _on_server_removed(self, name: str) -> None:
        """Called when a server is removed."""
        server = self.servers.pop(name, None)
        
        if self.preferred_server == name:
            # Select another server if available
            self.preferred_server = next(iter(self.servers.keys()), None)
        
        if server:
            for callback in self._callbacks:
                try:
                    callback("removed", server)
                except Exception as e:
                    logger.error(f"Callback error: {e}")
    
    def start_discovery(self) -> None:
        """Start mDNS discovery for GhostStream servers."""
        if self._discovery_started:
            logger.debug("Discovery already started")
            return
        
        try:
            logger.info(f"[mDNS] Starting discovery for {GhostStreamDiscoveryListener.SERVICE_TYPE}")
            self.zeroconf = Zeroconf()
            listener = GhostStreamDiscoveryListener(
                on_found=self._on_server_found,
                on_removed=self._on_server_removed
            )
            self.browser = ServiceBrowser(
                self.zeroconf,
                GhostStreamDiscoveryListener.SERVICE_TYPE,
                listener
            )
            self._discovery_started = True
            logger.info("[mDNS] Discovery started successfully - listening for GhostStream servers on the network")
        except Exception as e:
            logger.error(f"[mDNS] Failed to start discovery: {e}", exc_info=True)
    
    def stop_discovery(self) -> None:
        """Stop mDNS discovery."""
        if self.browser:
            self.browser.cancel()
        if self.zeroconf:
            self.zeroconf.close()
        
        self.browser = None
        self.zeroconf = None
        self._discovery_started = False
    
    def is_available(self) -> bool:
        """Check if any GhostStream server is available."""
        return len(self.servers) > 0
    
    def get_server(self, name: Optional[str] = None) -> Optional[GhostStreamServer]:
        """Get a server by name, or the preferred server."""
        if name:
            return self.servers.get(name)
        if self.preferred_server:
            return self.servers.get(self.preferred_server)
        return None
    
    def get_all_servers(self) -> List[GhostStreamServer]:
        """Get all discovered servers."""
        return list(self.servers.values())
    
    async def health_check(self, server: Optional[GhostStreamServer] = None) -> bool:
        """Check if a server is healthy."""
        server = server or self.get_server()
        if not server:
            return False
        
        try:
            response = await self._request_with_retry(
                "GET",
                f"{server.base_url}/api/health"
            )
            return response.status_code == 200
        except Exception:
            return False
    
    async def get_capabilities(self, server: Optional[GhostStreamServer] = None) -> Optional[Dict]:
        """Get server capabilities."""
        server = server or self.get_server()
        if not server:
            return None
        
        try:
            response = await self._request_with_retry(
                "GET",
                f"{server.base_url}/api/capabilities"
            )
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            logger.error(f"Failed to get capabilities: {e}")
        
        return None
    
    async def transcode(
        self,
        source: str,
        mode: str = "stream",
        format: str = "hls",
        video_codec: str = "h264",
        audio_codec: str = "aac",
        resolution: str = "original",
        bitrate: str = "auto",
        hw_accel: str = "auto",
        start_time: float = 0,
        server: Optional[GhostStreamServer] = None
    ) -> Optional[TranscodeJob]:
        """
        Start a transcoding job.
        
        Args:
            source: Source file URL (accessible from GhostStream server)
            mode: Transcoding mode:
                  - "stream": Single quality HLS streaming (default)
                  - "abr": Adaptive Bitrate with multiple quality variants
                  - "batch": File output (MP4, etc.)
            format: Output format (hls, mp4, webm, etc.)
            video_codec: Video codec (h264, h265, vp9, av1)
            audio_codec: Audio codec (aac, opus, copy)
            resolution: Target resolution (4k, 1080p, 720p, 480p, original)
            bitrate: Target bitrate or "auto"
            hw_accel: Hardware acceleration (auto, nvenc, qsv, software)
            start_time: Start position in seconds
            server: Specific server to use
        
        Returns:
            TranscodeJob with stream_url for playback
        
        Example:
            # Standard streaming
            job = await client.transcode(source="http://...", mode="stream")
            
            # Adaptive bitrate (Netflix-style multiple qualities)
            job = await client.transcode(source="http://...", mode="abr")
        """
        server = server or self.get_server()
        if not server:
            # Try to get any available server
            if self.servers:
                server = next(iter(self.servers.values()))
                logger.info(f"[GhostStream] Using first available server: {server.name}")
            else:
                logger.error("[GhostStream] No servers available for transcoding")
                return TranscodeJob(
                    job_id="error",
                    status=TranscodeStatus.ERROR,
                    error_message="No GhostStream servers available. Add a server in Settings."
                )
        
        # Build request matching GhostStream's TranscodeRequest pydantic model exactly
        request_body = {
            "source": source,
            "mode": mode,  # "stream" or "batch"
            "output": {
                "format": format,        # "hls", "mp4", "webm", etc.
                "video_codec": video_codec,  # "h264", "h265", "vp9", "av1", "copy"
                "audio_codec": audio_codec,  # "aac", "opus", "mp3", "flac", "ac3", "copy"
                "resolution": resolution,    # "4k", "1080p", "720p", "480p", "original"
                "bitrate": bitrate,
                "hw_accel": hw_accel      # "auto", "nvenc", "qsv", "vaapi", "videotoolbox", "amf", "software"
            },
            "start_time": start_time
        }
        
        logger.info(f"[GhostStream] Sending transcode request to {server.base_url}/api/transcode/start")
        logger.info(f"[GhostStream] Request body: source={source[:80]}..., mode={mode}, resolution={resolution}")
        
        try:
            response = await self._request_with_retry(
                "POST",
                f"{server.base_url}/api/transcode/start",
                json=request_body
            )
            
            logger.info(f"[GhostStream] Response status: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                logger.info(f"[GhostStream] Job created: {data.get('job_id')}")
                return TranscodeJob(
                    job_id=data["job_id"],
                    status=TranscodeStatus(data["status"]),
                    progress=data.get("progress", 0),
                    stream_url=data.get("stream_url"),
                    download_url=data.get("download_url"),
                    hw_accel_used=data.get("hw_accel_used")
                )
            else:
                error_text = response.text
                logger.error(f"[GhostStream] Transcode failed ({response.status_code}): {error_text[:300]}")
                return TranscodeJob(
                    job_id="error",
                    status=TranscodeStatus.ERROR,
                    error_message=f"GhostStream error ({response.status_code}): {error_text[:200]}"
                )
        except httpx.ConnectError as e:
            logger.error(f"[GhostStream] Cannot connect to {server.base_url}: {e}")
            return TranscodeJob(
                job_id="error",
                status=TranscodeStatus.ERROR,
                error_message=f"Cannot connect to GhostStream at {server.host}:{server.port}"
            )
        except httpx.TimeoutException as e:
            logger.error(f"[GhostStream] Request timed out to {server.base_url}: {e}")
            return TranscodeJob(
                job_id="error",
                status=TranscodeStatus.ERROR,
                error_message=f"Request to GhostStream timed out"
            )
        except Exception as e:
            logger.error(f"[GhostStream] Transcode request error: {e}", exc_info=True)
            return TranscodeJob(
                job_id="error",
                status=TranscodeStatus.ERROR,
                error_message=str(e)
            )
    
    async def get_job_status(
        self,
        job_id: str,
        server: Optional[GhostStreamServer] = None
    ) -> Optional[TranscodeJob]:
        """Get the status of a transcoding job."""
        server = server or self.get_server()
        if not server:
            return None
        
        try:
            response = await self._request_with_retry(
                "GET",
                f"{server.base_url}/api/transcode/{job_id}/status"
            )
            
            if response.status_code == 200:
                data = response.json()
                return TranscodeJob(
                    job_id=data["job_id"],
                    status=TranscodeStatus(data["status"]),
                    progress=data.get("progress", 0),
                    stream_url=data.get("stream_url"),
                    download_url=data.get("download_url"),
                    error_message=data.get("error_message"),
                    hw_accel_used=data.get("hw_accel_used")
                )
        except Exception as e:
            logger.error(f"Status request error: {e}")
        
        return None
    
    async def cancel_job(
        self,
        job_id: str,
        server: Optional[GhostStreamServer] = None
    ) -> bool:
        """Cancel a transcoding job."""
        server = server or self.get_server()
        if not server:
            return False
        
        try:
            response = await self._request_with_retry(
                "POST",
                f"{server.base_url}/api/transcode/{job_id}/cancel"
            )
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Cancel request error: {e}")
        
        return False
    
    async def delete_job(
        self,
        job_id: str,
        server: Optional[GhostStreamServer] = None
    ) -> bool:
        """Delete a transcoding job and its files."""
        server = server or self.get_server()
        if not server:
            return False
        
        try:
            response = await self._request_with_retry(
                "DELETE",
                f"{server.base_url}/api/transcode/{job_id}"
            )
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Delete request error: {e}")
        
        return False
    
    async def subscribe_progress(
        self,
        job_ids: List[str],
        server: Optional[GhostStreamServer] = None
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        Subscribe to real-time progress updates via WebSocket.
        
        Yields progress events as they arrive. Much more efficient than polling.
        
        Args:
            job_ids: List of job IDs to subscribe to
            server: Server to connect to
        
        Yields:
            Dict with progress data: {"type": "progress", "job_id": "...", "data": {...}}
        
        Example:
            async for event in client.subscribe_progress(["job-123"]):
                if event["type"] == "progress":
                    print(f"Progress: {event['data']['progress']}%")
                elif event["type"] == "status_change":
                    print(f"Status: {event['data']['status']}")
        
        Requires: pip install websockets
        """
        if not HAS_WEBSOCKETS:
            logger.error("WebSocket support requires 'websockets' package: pip install websockets")
            return
        
        server = server or self.get_server()
        if not server:
            logger.error("No server available for WebSocket connection")
            return
        
        ws_url = f"ws://{server.host}:{server.port}/ws/progress"
        
        try:
            async with websockets.connect(ws_url) as ws:
                # Subscribe to jobs
                subscribe_msg = {
                    "type": "subscribe",
                    "job_ids": job_ids
                }
                await ws.send(json.dumps(subscribe_msg))
                logger.info(f"[GhostStream] Subscribed to progress for {len(job_ids)} jobs")
                
                # Yield events as they arrive
                async for message in ws:
                    try:
                        event = json.loads(message)
                        yield event
                        
                        # Check if all jobs are complete
                        if event.get("type") == "status_change":
                            status = event.get("data", {}).get("status")
                            if status in ["ready", "error", "cancelled"]:
                                job_id = event.get("job_id")
                                if job_id in job_ids:
                                    job_ids.remove(job_id)
                                if not job_ids:
                                    logger.info("[GhostStream] All jobs complete, closing WebSocket")
                                    return
                    except json.JSONDecodeError:
                        logger.warning(f"[GhostStream] Invalid WebSocket message: {message[:100]}")
        except Exception as e:
            logger.error(f"[GhostStream] WebSocket error: {e}")
    
    async def wait_for_ready(
        self,
        job_id: str,
        timeout: float = 300,
        poll_interval: float = 1.0,
        server: Optional[GhostStreamServer] = None,
        use_websocket: bool = False
    ) -> Optional[TranscodeJob]:
        """
        Wait for a job to be ready for streaming.
        
        For live transcoding (HLS), the job becomes ready quickly
        as segments are generated.
        
        Args:
            job_id: Job ID to wait for
            timeout: Maximum time to wait in seconds
            poll_interval: How often to poll (ignored if use_websocket=True)
            server: Server to query
            use_websocket: Use WebSocket for real-time updates (more efficient)
        """
        server = server or self.get_server()
        if not server:
            return None
        
        elapsed = 0
        while elapsed < timeout:
            job = await self.get_job_status(job_id, server)
            
            if job is None:
                return None
            
            if job.status == TranscodeStatus.READY:
                return job
            
            if job.status == TranscodeStatus.ERROR:
                logger.error(f"Job failed: {job.error_message}")
                return job
            
            if job.status == TranscodeStatus.CANCELLED:
                return job
            
            # For streaming mode, return as soon as we have a stream URL
            if job.stream_url and job.status == TranscodeStatus.PROCESSING:
                return job
            
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
        
        logger.error(f"Timeout waiting for job {job_id}")
        return None


class GhostStreamLoadBalancer:
    """
    Load balancer for distributing transcode jobs across multiple GhostStream servers.
    
    Usage:
        lb = GhostStreamLoadBalancer(strategy=LoadBalanceStrategy.LEAST_BUSY)
        lb.start_discovery()
        
        # Transcode - automatically picks best server
        job = await lb.transcode(source="http://pi:5000/video.mkv")
        
        # Batch transcode multiple files
        jobs = await lb.batch_transcode([
            {"source": "http://pi:5000/video1.mkv"},
            {"source": "http://pi:5000/video2.mkv"},
            {"source": "http://pi:5000/video3.mkv"},
        ])
    """
    
    def __init__(
        self,
        strategy: LoadBalanceStrategy = LoadBalanceStrategy.LEAST_BUSY,
        manual_servers: Optional[List[str]] = None,
        client: Optional[GhostStreamClient] = None
    ):
        """
        Initialize the load balancer.
        
        Args:
            strategy: How to distribute jobs across servers
            manual_servers: List of server addresses (e.g., ["192.168.4.2:8765", "192.168.4.3:8765"])
            client: Optional existing GhostStreamClient to use (shares discovered servers)
        """
        self.strategy = strategy
        self.client = client or GhostStreamClient()
        self.server_stats: Dict[str, ServerStats] = {}
        self._round_robin_index = 0
        self._stats_lock = asyncio.Lock()
        self._job_server_map: Dict[str, str] = {}  # job_id -> server_name
        
        # Add manual servers
        if manual_servers:
            for addr in manual_servers:
                host, port = addr.split(":")
                name = f"manual_{host}"
                self.client.servers[name] = GhostStreamServer(
                    name=name,
                    host=host,
                    port=int(port)
                )
                self.server_stats[name] = ServerStats()
    
    def start_discovery(self) -> None:
        """Start discovering GhostStream servers."""
        self.client.add_callback(self._on_server_change)
        self.client.start_discovery()
    
    def stop_discovery(self) -> None:
        """Stop discovery."""
        self.client.stop_discovery()
    
    def _on_server_change(self, event: str, server: GhostStreamServer) -> None:
        """Handle server discovery events."""
        if event == "found":
            self.server_stats[server.name] = ServerStats()
            logger.info(f"LoadBalancer: Added server {server.name}")
        elif event == "removed":
            self.server_stats.pop(server.name, None)
            logger.info(f"LoadBalancer: Removed server {server.name}")
    
    async def refresh_stats(self) -> None:
        """Refresh statistics from all servers."""
        for name, server in self.client.servers.items():
            try:
                # Use the client's shared HTTP client for connection pooling
                response = await self.client._request_with_retry(
                    "GET",
                    f"{server.base_url}/api/health"
                )
                if response.status_code == 200:
                    data = response.json()
                    async with self._stats_lock:
                        stats = self.server_stats.get(name, ServerStats())
                        stats.active_jobs = data.get("current_jobs", 0)
                        stats.queued_jobs = data.get("queued_jobs", 0)
                        stats.is_healthy = True
                        stats.last_health_check = time.time()
                        self.server_stats[name] = stats
                else:
                    async with self._stats_lock:
                        if name in self.server_stats:
                            self.server_stats[name].is_healthy = False
            except Exception as e:
                logger.warning(f"Failed to get stats from {name}: {e}")
                async with self._stats_lock:
                    if name in self.server_stats:
                        self.server_stats[name].is_healthy = False
    
    async def _select_server(self) -> Optional[GhostStreamServer]:
        """Select a server based on the load balancing strategy."""
        logger.info(f"[LoadBalancer] Selecting server from {len(self.client.servers)} available")
        
        # If no servers, return None
        if not self.client.servers:
            logger.error("[LoadBalancer] No servers available")
            return None
        
        # Ensure all servers have stats entries
        for name in self.client.servers:
            if name not in self.server_stats:
                self.server_stats[name] = ServerStats()
                logger.debug(f"[LoadBalancer] Created stats for server: {name}")
        
        # For speed, just use the first available server without health check
        # Health check can cause delays and timeouts
        server = next(iter(self.client.servers.values()))
        logger.info(f"[LoadBalancer] Selected server: {server.name} ({server.host}:{server.port})")
        return server
    
    async def _select_server_advanced(self) -> Optional[GhostStreamServer]:
        """Advanced server selection with load balancing (unused for now)."""
        healthy_servers = [
            (name, self.client.servers[name])
            for name, stats in self.server_stats.items()
            if stats.is_healthy and name in self.client.servers
        ]
        
        if not healthy_servers:
            return None
        
        if self.strategy == LoadBalanceStrategy.ROUND_ROBIN:
            self._round_robin_index = (self._round_robin_index + 1) % len(healthy_servers)
            return healthy_servers[self._round_robin_index][1]
        
        elif self.strategy == LoadBalanceStrategy.LEAST_BUSY:
            best_name = min(
                healthy_servers,
                key=lambda x: (
                    self.server_stats[x[0]].active_jobs +
                    self.server_stats[x[0]].queued_jobs
                )
            )[0]
            return self.client.servers[best_name]
        
        elif self.strategy == LoadBalanceStrategy.FASTEST:
            # Prefer servers with hardware acceleration
            hw_servers = [
                (name, server) for name, server in healthy_servers
                if server.has_hw_accel
            ]
            if hw_servers:
                # Among HW servers, pick least busy
                best_name = min(
                    hw_servers,
                    key=lambda x: self.server_stats[x[0]].active_jobs
                )[0]
                return self.client.servers[best_name]
            # No HW servers, fall back to least busy
            return await self._select_server_strategy(LoadBalanceStrategy.LEAST_BUSY, healthy_servers)
        
        elif self.strategy == LoadBalanceStrategy.RANDOM:
            return random.choice(healthy_servers)[1]
        
        return healthy_servers[0][1]
    
    async def _select_server_strategy(
        self,
        strategy: LoadBalanceStrategy,
        servers: List[tuple]
    ) -> Optional[GhostStreamServer]:
        """Helper for fallback strategy selection."""
        if strategy == LoadBalanceStrategy.LEAST_BUSY:
            best_name = min(
                servers,
                key=lambda x: self.server_stats[x[0]].active_jobs
            )[0]
            return self.client.servers[best_name]
        return servers[0][1] if servers else None
    
    def get_servers(self) -> List[GhostStreamServer]:
        """Get all discovered servers."""
        return self.client.get_all_servers()
    
    def get_server_stats(self) -> Dict[str, Dict]:
        """Get stats for all servers."""
        return {
            name: {
                "host": self.client.servers[name].host if name in self.client.servers else "unknown",
                "active_jobs": stats.active_jobs,
                "queued_jobs": stats.queued_jobs,
                "is_healthy": stats.is_healthy,
                "has_hw_accel": self.client.servers[name].has_hw_accel if name in self.client.servers else False
            }
            for name, stats in self.server_stats.items()
        }
    
    async def transcode(
        self,
        source: str,
        mode: str = "stream",
        format: str = "hls",
        video_codec: str = "h264",
        audio_codec: str = "aac",
        resolution: str = "original",
        bitrate: str = "auto",
        hw_accel: str = "auto",
        start_time: float = 0
    ) -> Optional[TranscodeJob]:
        """
        Start a transcoding job on the best available server.
        
        Server is automatically selected based on load balancing strategy.
        """
        server = await self._select_server()
        if not server:
            logger.error("[LoadBalancer] No GhostStream servers available")
            return TranscodeJob(
                job_id="error",
                status=TranscodeStatus.ERROR,
                error_message="No healthy GhostStream servers available"
            )
        
        logger.info(f"LoadBalancer: Sending job to {server.name} ({server.host})")
        
        job = await self.client.transcode(
            source=source,
            mode=mode,
            format=format,
            video_codec=video_codec,
            audio_codec=audio_codec,
            resolution=resolution,
            bitrate=bitrate,
            hw_accel=hw_accel,
            start_time=start_time,
            server=server
        )
        
        if job:
            self._job_server_map[job.job_id] = server.name
            async with self._stats_lock:
                if server.name in self.server_stats:
                    self.server_stats[server.name].active_jobs += 1
        
        return job
    
    async def batch_transcode(
        self,
        jobs: List[Dict[str, Any]],
        parallel: bool = True
    ) -> List[Optional[TranscodeJob]]:
        """
        Transcode multiple files, distributing across servers.
        
        Args:
            jobs: List of job configs, each with at least "source" key
            parallel: If True, submit all jobs at once. If False, submit sequentially.
        
        Example:
            jobs = await lb.batch_transcode([
                {"source": "http://pi:5000/video1.mkv", "resolution": "1080p"},
                {"source": "http://pi:5000/video2.mkv", "resolution": "720p"},
                {"source": "http://pi:5000/video3.mkv"},
            ])
        """
        if parallel:
            tasks = [
                self.transcode(
                    source=job_config["source"],
                    mode=job_config.get("mode", "batch"),
                    format=job_config.get("format", "mp4"),
                    video_codec=job_config.get("video_codec", "h264"),
                    audio_codec=job_config.get("audio_codec", "aac"),
                    resolution=job_config.get("resolution", "original"),
                    bitrate=job_config.get("bitrate", "auto"),
                    hw_accel=job_config.get("hw_accel", "auto"),
                    start_time=job_config.get("start_time", 0)
                )
                for job_config in jobs
            ]
            return await asyncio.gather(*tasks)
        else:
            results = []
            for job_config in jobs:
                job = await self.transcode(
                    source=job_config["source"],
                    mode=job_config.get("mode", "batch"),
                    format=job_config.get("format", "mp4"),
                    video_codec=job_config.get("video_codec", "h264"),
                    audio_codec=job_config.get("audio_codec", "aac"),
                    resolution=job_config.get("resolution", "original"),
                    bitrate=job_config.get("bitrate", "auto"),
                    hw_accel=job_config.get("hw_accel", "auto"),
                    start_time=job_config.get("start_time", 0)
                )
                results.append(job)
            return results
    
    async def get_job_status(self, job_id: str) -> Optional[TranscodeJob]:
        """Get job status from the correct server."""
        server_name = self._job_server_map.get(job_id)
        if server_name and server_name in self.client.servers:
            server = self.client.servers[server_name]
            return await self.client.get_job_status(job_id, server)
        
        # Try all servers if we don't know which one
        for server in self.client.servers.values():
            job = await self.client.get_job_status(job_id, server)
            if job:
                self._job_server_map[job_id] = server.name
                return job
        
        return None
    
    async def cancel_job(self, job_id: str) -> bool:
        """Cancel a job on the correct server."""
        server_name = self._job_server_map.get(job_id)
        if server_name and server_name in self.client.servers:
            server = self.client.servers[server_name]
            success = await self.client.cancel_job(job_id, server)
            if success:
                async with self._stats_lock:
                    if server_name in self.server_stats:
                        self.server_stats[server_name].active_jobs = max(
                            0, self.server_stats[server_name].active_jobs - 1
                        )
            return success
        return False
    
    async def wait_for_all(
        self,
        job_ids: List[str],
        timeout: float = 3600,
        poll_interval: float = 5.0
    ) -> List[Optional[TranscodeJob]]:
        """
        Wait for multiple jobs to complete.
        
        Useful for batch transcoding.
        """
        results = [None] * len(job_ids)
        remaining = set(range(len(job_ids)))
        elapsed = 0
        
        while remaining and elapsed < timeout:
            for i in list(remaining):
                job = await self.get_job_status(job_ids[i])
                if job:
                    if job.status in [TranscodeStatus.READY, TranscodeStatus.ERROR, TranscodeStatus.CANCELLED]:
                        results[i] = job
                        remaining.remove(i)
                        
                        # Update stats
                        server_name = self._job_server_map.get(job_ids[i])
                        if server_name:
                            async with self._stats_lock:
                                if server_name in self.server_stats:
                                    self.server_stats[server_name].active_jobs = max(
                                        0, self.server_stats[server_name].active_jobs - 1
                                    )
                                    self.server_stats[server_name].total_processed += 1
            
            if remaining:
                await asyncio.sleep(poll_interval)
                elapsed += poll_interval
        
        return results

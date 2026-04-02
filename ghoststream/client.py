"""
GhostStream Client - For GhostHub and other media servers to discover and use GhostStream

Pure Specter-native: gevent + Flask. No asyncio, no threading primitives.

Usage in GhostHub:
    from ghoststream.client import GhostStreamClient

    client = GhostStreamClient()
    client.start_discovery()

    if client.is_available():
        job = client.transcode(
            source="http://pi-ip:5000/media/video.mkv",
            resolution="1080p"
        )
        # Use job.stream_url in your video player
"""

import logging
import random
import socket
from typing import Optional, Dict, Any, List, Callable
from dataclasses import dataclass, field
from enum import Enum

import gevent
import gevent.lock
import gevent.pool
from gevent import sleep as gevent_sleep

import httpx
from zeroconf import ServiceBrowser, ServiceListener, Zeroconf

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
    ROUND_ROBIN = "round_robin"
    LEAST_BUSY = "least_busy"
    FASTEST = "fastest"
    RANDOM = "random"


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
    control_token: Optional[str] = None
    error_message: Optional[str] = None
    hw_accel_used: Optional[str] = None
    duration: Optional[float] = None
    current_time: Optional[float] = None
    eta_seconds: Optional[int] = None
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    start_time: float = 0
    is_shared: bool = False
    viewer_count: int = 1
    variants: Optional[List[Dict[str, Any]]] = None
    media_info: Optional[Dict[str, Any]] = None
    subtitles: Optional[List[Dict[str, Any]]] = None


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
    """Configuration for GhostStreamClient."""
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT
    read_timeout: float = DEFAULT_READ_TIMEOUT
    write_timeout: float = DEFAULT_WRITE_TIMEOUT
    max_retries: int = DEFAULT_MAX_RETRIES
    retry_delay: float = DEFAULT_RETRY_DELAY
    retry_max_delay: float = DEFAULT_RETRY_MAX_DELAY
    retry_multiplier: float = DEFAULT_RETRY_MULTIPLIER
    retry_on_status: List[int] = field(default_factory=lambda: [502, 503, 504])
    client_name: Optional[str] = None
    max_connections: int = 20
    max_keepalive_connections: int = 10
    max_inflight_requests: int = 20


class GhostStreamClient:
    """
    Client for discovering and using GhostStream transcoding services.

    Pure Specter-native: all locking via gevent.lock.BoundedSemaphore,
    all sleeping via gevent.sleep. No asyncio, no threading primitives.
    """

    def __init__(
        self,
        manual_server: Optional[str] = None,
        config: Optional[ClientConfig] = None
    ):
        self.config = config or ClientConfig()
        self.servers: Dict[str, GhostStreamServer] = {}
        self.preferred_server: Optional[str] = None
        self.zeroconf: Optional[Zeroconf] = None
        self.browser: Optional[ServiceBrowser] = None
        self._discovery_started = False
        self._callbacks: List[Callable[[str, GhostStreamServer], None]] = []
        self._control_tokens: Dict[str, str] = {}
        self._job_slot_modes: Dict[str, str] = {}

        # One-at-a-time HTTP client access via gevent semaphore
        self._http_client: Optional[httpx.Client] = None
        self._client_lock = gevent.lock.BoundedSemaphore(1)
        self._request_slots = gevent.lock.BoundedSemaphore(
            max(1, self.config.max_inflight_requests)
        )
        self._job_limiters: Dict[str, gevent.lock.BoundedSemaphore] = {}

        if manual_server:
            host, port = manual_server.split(":")
            self.servers["manual"] = GhostStreamServer(
                name="manual",
                host=host,
                port=int(port)
            )
            self.preferred_server = "manual"
            self._ensure_job_limiter(self.servers["manual"])

    # =========================================================================
    # Control token helpers
    # =========================================================================

    def _token_key(self, server: GhostStreamServer, job_id: str) -> str:
        return f"{server.base_url}|{job_id}"

    def _store_control_token(self, server, job_id, control_token) -> None:
        if not server or not job_id or not control_token:
            return
        self._control_tokens[self._token_key(server, job_id)] = control_token

    def _get_control_token(self, server, job_id: str) -> Optional[str]:
        if not server:
            return None
        return self._control_tokens.get(self._token_key(server, job_id))

    def _drop_control_token(self, server, job_id: str) -> None:
        if not server:
            return
        self._control_tokens.pop(self._token_key(server, job_id), None)

    def _drop_server_tokens(self, server: GhostStreamServer) -> None:
        prefix = f"{server.base_url}|"
        for key in [k for k in self._control_tokens if k.startswith(prefix)]:
            self._control_tokens.pop(key, None)
            self._job_slot_modes.pop(key, None)

    def _auth_headers(self, server, job_id: str) -> Dict[str, str]:
        token = self._get_control_token(server, job_id)
        return {"X-GhostStream-Control-Token": token} if token else {}

    def resolve_job_server(self, job_id: str) -> Optional[GhostStreamServer]:
        """Resolve a job's owning server from in-memory token or slot tracking."""
        if not job_id:
            return None

        suffix = f"|{job_id}"
        tracked_keys = list(self._control_tokens.keys()) + list(self._job_slot_modes.keys())
        for key in tracked_keys:
            if not key.endswith(suffix):
                continue

            base_url = key[:-len(suffix)]
            for server in self.servers.values():
                if server.base_url == base_url:
                    return server

        return None

    def get_job_auth_headers(
        self,
        job_id: str,
        server: Optional[GhostStreamServer] = None
    ) -> Dict[str, str]:
        """Return auth headers for a job when a control token is known."""
        resolved_server = server or self.resolve_job_server(job_id)
        return self._auth_headers(resolved_server, job_id) if resolved_server else {}

    def _job_from_payload(self, data: Dict[str, Any], server) -> TranscodeJob:
        job = TranscodeJob(
            job_id=data["job_id"],
            status=TranscodeStatus(data["status"]),
            progress=data.get("progress", 0),
            stream_url=data.get("stream_url"),
            download_url=data.get("download_url"),
            control_token=data.get("control_token"),
            error_message=data.get("error_message"),
            hw_accel_used=data.get("hw_accel_used"),
            duration=data.get("duration"),
            current_time=data.get("current_time"),
            eta_seconds=data.get("eta_seconds"),
            created_at=str(data["created_at"]) if data.get("created_at") else None,
            started_at=str(data["started_at"]) if data.get("started_at") else None,
            completed_at=str(data["completed_at"]) if data.get("completed_at") else None,
            start_time=data.get("start_time", 0),
            is_shared=data.get("is_shared", False),
            viewer_count=data.get("viewer_count", 1),
            variants=data.get("variants"),
            media_info=data.get("media_info"),
            subtitles=data.get("subtitles"),
        )
        self._store_control_token(server, job.job_id, job.control_token)
        return job

    # =========================================================================
    # HTTP client (gevent-safe connection pool)
    # =========================================================================

    def _ensure_job_limiter(self, server: GhostStreamServer) -> gevent.lock.BoundedSemaphore:
        limiter = self._job_limiters.get(server.name)
        if limiter is None:
            limiter = gevent.lock.BoundedSemaphore(max(1, server.max_jobs))
            self._job_limiters[server.name] = limiter
        return limiter

    def _reserve_job_slot(self, server: GhostStreamServer) -> None:
        self._ensure_job_limiter(server).acquire()

    def _release_job_slot(self, server: Optional[GhostStreamServer], job_id: str) -> None:
        if not server:
            return
        key = self._token_key(server, job_id)
        if key not in self._job_slot_modes:
            return
        limiter = self._job_limiters.get(server.name)
        self._job_slot_modes.pop(key, None)
        if limiter is not None:
            try:
                limiter.release()
            except ValueError:
                logger.debug(
                    "[GhostStream] Ignored extra job-slot release for %s on %s",
                    job_id,
                    server.name,
                )

    def _get_client(self) -> httpx.Client:
        """Get or create the shared HTTP client. Protected by BoundedSemaphore."""
        with self._client_lock:
            if self._http_client is None or self._http_client.is_closed:
                timeout = httpx.Timeout(
                    connect=self.config.connect_timeout,
                    read=self.config.read_timeout,
                    write=self.config.write_timeout,
                    pool=self.config.connect_timeout
                )
                self._http_client = httpx.Client(
                    timeout=timeout,
                    limits=httpx.Limits(
                        max_connections=max(1, self.config.max_connections),
                        max_keepalive_connections=max(1, self.config.max_keepalive_connections),
                    )
                )
            return self._http_client

    def close(self) -> None:
        """Close the HTTP client and stop discovery."""
        with self._client_lock:
            if self._http_client and not self._http_client.is_closed:
                self._http_client.close()
                self._http_client = None
        self.stop_discovery()

    def __enter__(self) -> "GhostStreamClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.close()
        return False

    def _request_with_retry(self, method: str, url: str, **kwargs) -> httpx.Response:
        """
        HTTP request with exponential backoff retry. Uses gevent.sleep for yielding.
        """
        client = self._get_client()
        last_exception = None
        delay = self.config.retry_delay

        if self.config.client_name:
            headers = kwargs.pop("headers", {})
            headers["X-GhostStream-Client"] = self.config.client_name
            kwargs["headers"] = headers

        for attempt in range(self.config.max_retries + 1):
            try:
                with self._request_slots:
                    response = client.request(method, url, **kwargs)

                if response.status_code in self.config.retry_on_status:
                    if attempt < self.config.max_retries:
                        logger.warning(
                            f"[GhostStream] {response.status_code} from {url}, "
                            f"retrying ({attempt + 1}/{self.config.max_retries})..."
                        )
                        gevent_sleep(delay + random.uniform(0, 1))
                        delay = min(delay * self.config.retry_multiplier, self.config.retry_max_delay)
                        continue

                return response

            except (httpx.ConnectError, httpx.ConnectTimeout) as e:
                last_exception = e
                if attempt < self.config.max_retries:
                    logger.warning(
                        f"[GhostStream] Connection failed, retrying ({attempt + 1}/{self.config.max_retries})..."
                    )
                    gevent_sleep(delay + random.uniform(0, 1))
                    delay = min(delay * self.config.retry_multiplier, self.config.retry_max_delay)
                else:
                    raise
            except httpx.TimeoutException as e:
                last_exception = e
                if attempt < self.config.max_retries:
                    logger.warning(
                        f"[GhostStream] Request timed out, retrying ({attempt + 1}/{self.config.max_retries})..."
                    )
                    gevent_sleep(delay)
                    delay = min(delay * self.config.retry_multiplier, self.config.retry_max_delay)
                else:
                    raise

        if last_exception:
            raise last_exception
        raise RuntimeError("Unexpected retry loop exit")

    # =========================================================================
    # Discovery
    # =========================================================================

    def add_callback(self, callback: Callable[[str, GhostStreamServer], None]) -> None:
        self._callbacks.append(callback)

    def _on_server_found(self, server: GhostStreamServer) -> None:
        self.servers[server.name] = server
        self._ensure_job_limiter(server)
        if self.preferred_server is None:
            self.preferred_server = server.name
        elif server.has_hw_accel and not self.get_server().has_hw_accel:
            self.preferred_server = server.name
        for cb in self._callbacks:
            try:
                cb("found", server)
            except Exception as e:
                logger.error(f"Callback error: {e}")

    def _on_server_removed(self, name: str) -> None:
        server = self.servers.pop(name, None)
        if server:
            self._drop_server_tokens(server)
            self._job_limiters.pop(server.name, None)
        if self.preferred_server == name:
            self.preferred_server = next(iter(self.servers.keys()), None)
        if server:
            for cb in self._callbacks:
                try:
                    cb("removed", server)
                except Exception as e:
                    logger.error(f"Callback error: {e}")

    def start_discovery(self) -> None:
        """Start mDNS discovery for GhostStream servers on the LAN."""
        if self._discovery_started:
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
            logger.info("[mDNS] Discovery started")
        except Exception as e:
            logger.error(f"[mDNS] Failed to start discovery: {e}", exc_info=True)

    def stop_discovery(self) -> None:
        """Stop mDNS discovery and unregister from zeroconf."""
        if self.browser:
            self.browser.cancel()
        if self.zeroconf:
            self.zeroconf.close()
        self.browser = None
        self.zeroconf = None
        self._discovery_started = False

    def is_available(self) -> bool:
        return len(self.servers) > 0

    def get_server(self, name: Optional[str] = None) -> Optional[GhostStreamServer]:
        if name:
            return self.servers.get(name)
        if self.preferred_server:
            return self.servers.get(self.preferred_server)
        return None

    def get_all_servers(self) -> List[GhostStreamServer]:
        return list(self.servers.values())

    # =========================================================================
    # Transcode API
    # =========================================================================

    def health_check(self, server: Optional[GhostStreamServer] = None) -> bool:
        server = server or self.get_server()
        if not server:
            return False
        try:
            response = self._request_with_retry("GET", f"{server.base_url}/api/health")
            return response.status_code == 200
        except Exception:
            return False

    def get_health(self, server: Optional[GhostStreamServer] = None) -> Optional[Dict]:
        """Get full health status including uptime, job counts."""
        server = server or self.get_server()
        if not server:
            return None
        try:
            response = self._request_with_retry("GET", f"{server.base_url}/api/health")
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            logger.error(f"Failed to get health: {e}")
        return None

    def get_health_detailed(self, server: Optional[GhostStreamServer] = None) -> Optional[Dict]:
        """Get detailed health including per-component status."""
        server = server or self.get_server()
        if not server:
            return None
        try:
            response = self._request_with_retry("GET", f"{server.base_url}/api/health/detailed")
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            logger.error(f"Failed to get detailed health: {e}")
        return None

    def get_capabilities(self, server: Optional[GhostStreamServer] = None) -> Optional[Dict]:
        server = server or self.get_server()
        if not server:
            return None
        try:
            response = self._request_with_retry("GET", f"{server.base_url}/api/capabilities")
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            logger.error(f"Failed to get capabilities: {e}")
        return None

    def get_stats(self, server: Optional[GhostStreamServer] = None) -> Optional[Dict]:
        """Get server statistics (uptime, jobs processed, throughput)."""
        server = server or self.get_server()
        if not server:
            return None
        try:
            response = self._request_with_retry("GET", f"{server.base_url}/api/stats")
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            logger.error(f"Failed to get stats: {e}")
        return None

    def get_shared_streams(self, server: Optional[GhostStreamServer] = None) -> Optional[Dict]:
        """Get active shared stream information."""
        server = server or self.get_server()
        if not server:
            return None
        try:
            response = self._request_with_retry("GET", f"{server.base_url}/api/streams/shared")
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            logger.error(f"Failed to get shared streams: {e}")
        return None

    def get_stream_info(self, job_id: str, server: Optional[GhostStreamServer] = None) -> Optional[Dict]:
        """Get stream metadata for a specific job."""
        server = server or self.get_server()
        if not server:
            return None
        try:
            response = self._request_with_retry(
                "GET",
                f"{server.base_url}/api/transcode/{job_id}/stream",
                headers=self._auth_headers(server, job_id),
            )
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            logger.error(f"Failed to get stream info: {e}")
        return None

    def leave_stream(self, job_id: str, session_id: Optional[str] = None, server: Optional[GhostStreamServer] = None) -> bool:
        """Leave a shared stream."""
        server = server or self.get_server()
        if not server:
            return False
        try:
            body = {"session_id": session_id} if session_id else {}
            response = self._request_with_retry(
                "POST",
                f"{server.base_url}/api/transcode/{job_id}/leave",
                headers=self._auth_headers(server, job_id),
                json=body,
            )
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Failed to leave stream: {e}")
        return False

    def get_cleanup_stats(self, server: Optional[GhostStreamServer] = None) -> Optional[Dict]:
        """Get cleanup statistics."""
        server = server or self.get_server()
        if not server:
            return None
        try:
            response = self._request_with_retry("GET", f"{server.base_url}/api/cleanup/stats")
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            logger.error(f"Failed to get cleanup stats: {e}")
        return None

    def run_cleanup(self, server: Optional[GhostStreamServer] = None) -> Optional[Dict]:
        """Trigger a cleanup run."""
        server = server or self.get_server()
        if not server:
            return None
        try:
            response = self._request_with_retry("POST", f"{server.base_url}/api/cleanup/run")
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            logger.error(f"Failed to run cleanup: {e}")
        return None

    def transcode(
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
        subtitles: Optional[List[Dict]] = None,
        session_id: Optional[str] = None,
        server: Optional[GhostStreamServer] = None
    ) -> Optional[TranscodeJob]:
        server = server or self.get_server()
        if not server:
            if self.servers:
                server = next(iter(self.servers.values()))
            else:
                logger.error("[GhostStream] No servers available")
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
            "subtitles": subtitles,
            "start_time": start_time,
            "session_id": session_id
        }

        logger.info(f"[GhostStream] POST {server.base_url}/api/transcode/start — source={source[:80]}... mode={mode} res={resolution}")
        self._reserve_job_slot(server)

        try:
            response = self._request_with_retry(
                "POST",
                f"{server.base_url}/api/transcode/start",
                json=request_body
            )
            if response.status_code == 200:
                data = response.json()
                logger.info(f"[GhostStream] Job created: {data.get('job_id')}")
                job = self._job_from_payload(data, server)
                self._job_slot_modes[self._token_key(server, job.job_id)] = mode
                return job
            else:
                error_text = response.text
                logger.error(f"[GhostStream] Transcode failed ({response.status_code}): {error_text[:300]}")
                self._release_job_slot(server, "error")
                return TranscodeJob(
                    job_id="error",
                    status=TranscodeStatus.ERROR,
                    error_message=f"GhostStream error ({response.status_code}): {error_text[:200]}"
                )
        except httpx.ConnectError:
            logger.error(f"[GhostStream] Cannot connect to {server.base_url}")
            job = TranscodeJob(
                job_id="error",
                status=TranscodeStatus.ERROR,
                error_message=f"Cannot connect to GhostStream at {server.host}:{server.port}"
            )
            return job
        except httpx.TimeoutException:
            logger.error(f"[GhostStream] Request timed out to {server.base_url}")
            job = TranscodeJob(
                job_id="error",
                status=TranscodeStatus.ERROR,
                error_message="Request to GhostStream timed out"
            )
            return job
        except Exception as e:
            logger.error(f"[GhostStream] Transcode request error: {e}", exc_info=True)
            job = TranscodeJob(
                job_id="error",
                status=TranscodeStatus.ERROR,
                error_message=str(e)
            )
            return job
        finally:
            if "job" not in locals() or job.job_id == "error":
                try:
                    self._ensure_job_limiter(server).release()
                except ValueError:
                    logger.debug(
                        "[GhostStream] Job slot already released after failed create on %s",
                        server.name,
                    )

    def get_job_status(
        self,
        job_id: str,
        server: Optional[GhostStreamServer] = None
    ) -> Optional[TranscodeJob]:
        server = server or self.get_server()
        if not server:
            return None
        try:
            response = self._request_with_retry(
                "GET",
                f"{server.base_url}/api/transcode/{job_id}/status",
                headers=self._auth_headers(server, job_id),
            )
            if response.status_code == 200:
                job = self._job_from_payload(response.json(), server)
                if job.status in (TranscodeStatus.ERROR, TranscodeStatus.CANCELLED):
                    self._release_job_slot(server, job.job_id)
                elif job.status == TranscodeStatus.READY:
                    mode = self._job_slot_modes.get(self._token_key(server, job.job_id))
                    if mode and mode != "stream":
                        self._release_job_slot(server, job.job_id)
                return job
        except Exception as e:
            logger.error(f"Status request error: {e}")
        return None

    def cancel_job(
        self,
        job_id: str,
        server: Optional[GhostStreamServer] = None
    ) -> bool:
        server = server or self.get_server()
        if not server:
            return False
        try:
            response = self._request_with_retry(
                "POST",
                f"{server.base_url}/api/transcode/{job_id}/cancel",
                headers=self._auth_headers(server, job_id),
            )
            success = response.status_code == 200
            if success:
                self._drop_control_token(server, job_id)
                self._release_job_slot(server, job_id)
            return success
        except Exception as e:
            logger.error(f"Cancel request error: {e}")
        return False

    def delete_job(
        self,
        job_id: str,
        server: Optional[GhostStreamServer] = None
    ) -> bool:
        server = server or self.get_server()
        if not server:
            return False
        try:
            response = self._request_with_retry(
                "DELETE",
                f"{server.base_url}/api/transcode/{job_id}",
                headers=self._auth_headers(server, job_id),
            )
            success = response.status_code == 200
            if success:
                self._drop_control_token(server, job_id)
                self._release_job_slot(server, job_id)
            return success
        except Exception as e:
            logger.error(f"Delete request error: {e}")
        return False

    def wait_for_ready(
        self,
        job_id: str,
        timeout: float = 300,
        poll_interval: float = 1.0,
        server: Optional[GhostStreamServer] = None
    ) -> Optional[TranscodeJob]:
        """Poll until the job is ready, yielding to the gevent hub between polls."""
        server = server or self.get_server()
        if not server:
            return None

        elapsed = 0.0
        while elapsed < timeout:
            job = self.get_job_status(job_id, server)
            if job is None:
                return None
            if job.status == TranscodeStatus.READY:
                return job
            if job.status == TranscodeStatus.ERROR:
                logger.error(f"Job {job_id} failed: {job.error_message}")
                return job
            if job.status == TranscodeStatus.CANCELLED:
                return job
            # Streaming mode: stream URL available before READY
            if job.stream_url and job.status == TranscodeStatus.PROCESSING:
                return job
            gevent_sleep(poll_interval)
            elapsed += poll_interval

        logger.error(f"Timeout waiting for job {job_id}")
        return None


class GhostStreamLoadBalancer:
    """
    Load balancer for distributing transcode jobs across multiple GhostStream servers.

    Pure Specter-native: gevent.lock.BoundedSemaphore for mutual exclusion,
    gevent.spawn for background stat refresh, gevent.sleep for polling.

    Usage:
        lb = GhostStreamLoadBalancer(strategy=LoadBalanceStrategy.LEAST_BUSY)
        lb.start_discovery()

        job = lb.transcode(source="http://pi:5000/video.mkv")

        jobs = lb.batch_transcode([
            {"source": "http://pi:5000/video1.mkv"},
            {"source": "http://pi:5000/video2.mkv"},
        ])
    """

    def __init__(
        self,
        strategy: LoadBalanceStrategy = LoadBalanceStrategy.LEAST_BUSY,
        manual_servers: Optional[List[str]] = None,
        client: Optional[GhostStreamClient] = None
    ):
        self.strategy = strategy
        self.client = client or GhostStreamClient()
        self.server_stats: Dict[str, ServerStats] = {}
        self._round_robin_index = 0
        self._stats_lock = gevent.lock.BoundedSemaphore(1)
        self._job_server_map: Dict[str, str] = {}
        self._stats_cache_ttl = 5.0
        self._last_stats_refresh = 0.0

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
        self.client.add_callback(self._on_server_change)
        self.client.start_discovery()

    def stop_discovery(self) -> None:
        self.client.stop_discovery()

    def _on_server_change(self, event: str, server: GhostStreamServer) -> None:
        if event == "found":
            self.server_stats[server.name] = ServerStats()
            logger.info(f"LoadBalancer: Added server {server.name}")
        elif event == "removed":
            self.server_stats.pop(server.name, None)
            logger.info(f"LoadBalancer: Removed server {server.name}")

    def refresh_stats(self) -> None:
        """Pull health stats from every server. Called in a greenlet."""
        for name, server in list(self.client.servers.items()):
            try:
                response = self.client._request_with_retry("GET", f"{server.base_url}/api/health")
                if response.status_code == 200:
                    data = response.json()
                    with self._stats_lock:
                        stats = self.server_stats.get(name, ServerStats())
                        stats.active_jobs = data.get("current_jobs", 0)
                        stats.queued_jobs = data.get("queued_jobs", 0)
                        stats.is_healthy = True
                        import time as _time
                        stats.last_health_check = _time.time()
                        self.server_stats[name] = stats
                else:
                    with self._stats_lock:
                        if name in self.server_stats:
                            self.server_stats[name].is_healthy = False
            except Exception as e:
                logger.warning(f"LoadBalancer: Failed to get stats from {name}: {e}")
                with self._stats_lock:
                    if name in self.server_stats:
                        self.server_stats[name].is_healthy = False

    def _maybe_refresh_stats(self) -> None:
        """Spawn a background greenlet to refresh stats if cache has expired."""
        import time as _time
        if _time.time() - self._last_stats_refresh > self._stats_cache_ttl:
            self._last_stats_refresh = _time.time()
            gevent.spawn(self.refresh_stats)

    def _select_server(self) -> Optional[GhostStreamServer]:
        logger.info(f"[LoadBalancer] Selecting from {len(self.client.servers)} server(s)")

        if not self.client.servers:
            logger.error("[LoadBalancer] No servers available")
            return None

        for name in self.client.servers:
            if name not in self.server_stats:
                self.server_stats[name] = ServerStats()

        self._maybe_refresh_stats()

        healthy = [
            (name, self.client.servers[name])
            for name, stats in self.server_stats.items()
            if stats.is_healthy and name in self.client.servers
        ]

        if not healthy:
            logger.warning("[LoadBalancer] No healthy servers, using all available")
            healthy = list(self.client.servers.items())

        if not healthy:
            return None

        if self.strategy == LoadBalanceStrategy.ROUND_ROBIN:
            self._round_robin_index = (self._round_robin_index + 1) % len(healthy)
            return healthy[self._round_robin_index][1]

        if self.strategy == LoadBalanceStrategy.LEAST_BUSY:
            best = min(
                healthy,
                key=lambda x: self.server_stats[x[0]].active_jobs + self.server_stats[x[0]].queued_jobs
            )
            return best[1]

        if self.strategy == LoadBalanceStrategy.FASTEST:
            hw = [(n, s) for n, s in healthy if s.has_hw_accel]
            pool = hw if hw else healthy
            best = min(pool, key=lambda x: self.server_stats[x[0]].active_jobs)
            return best[1]

        if self.strategy == LoadBalanceStrategy.RANDOM:
            return random.choice(healthy)[1]

        return healthy[0][1]

    def get_servers(self) -> List[GhostStreamServer]:
        return self.client.get_all_servers()

    def get_server_stats(self) -> Dict[str, Dict]:
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

    def transcode(
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
        subtitles: Optional[List[Dict]] = None
    ) -> Optional[TranscodeJob]:
        """Submit a transcode job to the best available server."""
        server = self._select_server()
        if not server:
            logger.error("[LoadBalancer] No GhostStream servers available")
            return TranscodeJob(
                job_id="error",
                status=TranscodeStatus.ERROR,
                error_message="No healthy GhostStream servers available"
            )

        logger.info(f"LoadBalancer: Dispatching job to {server.name} ({server.host})")

        job = self.client.transcode(
            source=source,
            mode=mode,
            format=format,
            video_codec=video_codec,
            audio_codec=audio_codec,
            resolution=resolution,
            bitrate=bitrate,
            hw_accel=hw_accel,
            start_time=start_time,
            subtitles=subtitles,
            server=server
        )

        if job and job.job_id != "error":
            self._job_server_map[job.job_id] = server.name
            with self._stats_lock:
                if server.name in self.server_stats:
                    self.server_stats[server.name].active_jobs += 1

        return job

    def batch_transcode(
        self,
        jobs: List[Dict[str, Any]],
        parallel: bool = True
    ) -> List[Optional[TranscodeJob]]:
        """
        Transcode multiple files, distributing across servers.

        When parallel=True, all jobs are spawned as greenlets and joined.
        """
        def _submit(job_config: Dict[str, Any]) -> Optional[TranscodeJob]:
            return self.transcode(
                source=job_config["source"],
                mode=job_config.get("mode", "batch"),
                format=job_config.get("format", "mp4"),
                video_codec=job_config.get("video_codec", "h264"),
                audio_codec=job_config.get("audio_codec", "aac"),
                resolution=job_config.get("resolution", "original"),
                bitrate=job_config.get("bitrate", "auto"),
                hw_accel=job_config.get("hw_accel", "auto"),
                start_time=job_config.get("start_time", 0),
                subtitles=job_config.get("subtitles")
            )

        if parallel:
            greenlets = [gevent.spawn(_submit, job_config) for job_config in jobs]
            gevent.joinall(greenlets)
            return [g.value for g in greenlets]

        return [_submit(job_config) for job_config in jobs]

    def get_job_status(self, job_id: str) -> Optional[TranscodeJob]:
        """Get job status from the server that owns the job."""
        server_name = self._job_server_map.get(job_id)
        if server_name and server_name in self.client.servers:
            return self.client.get_job_status(job_id, self.client.servers[server_name])

        # Broadcast search across all servers
        for server in self.client.servers.values():
            job = self.client.get_job_status(job_id, server)
            if job:
                self._job_server_map[job_id] = server.name
                return job

        return None

    def cancel_job(self, job_id: str) -> bool:
        """Cancel a job on its owning server."""
        server_name = self._job_server_map.get(job_id)
        if not server_name or server_name not in self.client.servers:
            return False

        server = self.client.servers[server_name]
        success = self.client.cancel_job(job_id, server)
        if success:
            with self._stats_lock:
                if server_name in self.server_stats:
                    self.server_stats[server_name].active_jobs = max(
                        0, self.server_stats[server_name].active_jobs - 1
                    )
        return success

    def wait_for_all(
        self,
        job_ids: List[str],
        timeout: float = 3600,
        poll_interval: float = 5.0
    ) -> List[Optional[TranscodeJob]]:
        """
        Wait for multiple jobs to complete, yielding to the gevent hub between polls.
        """
        results: List[Optional[TranscodeJob]] = [None] * len(job_ids)
        remaining = set(range(len(job_ids)))
        elapsed = 0.0

        while remaining and elapsed < timeout:
            for i in list(remaining):
                job = self.get_job_status(job_ids[i])
                if job and job.status in (
                    TranscodeStatus.READY,
                    TranscodeStatus.ERROR,
                    TranscodeStatus.CANCELLED
                ):
                    results[i] = job
                    remaining.remove(i)
                    server_name = self._job_server_map.get(job_ids[i])
                    if server_name:
                        with self._stats_lock:
                            if server_name in self.server_stats:
                                self.server_stats[server_name].active_jobs = max(
                                    0, self.server_stats[server_name].active_jobs - 1
                                )
                                self.server_stats[server_name].total_processed += 1

            if remaining:
                gevent_sleep(poll_interval)
                elapsed += poll_interval

        return results

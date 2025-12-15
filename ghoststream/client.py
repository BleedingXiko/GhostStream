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
from typing import Optional, Dict, Any, List, Callable
from dataclasses import dataclass
from enum import Enum

import httpx
from zeroconf import ServiceBrowser, ServiceListener, Zeroconf
import socket

logger = logging.getLogger(__name__)


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
        info = zc.get_service_info(type_, name)
        if info:
            addresses = [socket.inet_ntoa(addr) for addr in info.addresses]
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
                
                logger.info(f"Found GhostStream: {server.host}:{server.port}")
                self.on_found(server)
    
    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        logger.info(f"GhostStream removed: {name}")
        self.on_removed(name)
    
    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        self.add_service(zc, type_, name)


class GhostStreamClient:
    """
    Client for discovering and using GhostStream transcoding services.
    
    Designed for integration with GhostHub and other media servers.
    """
    
    def __init__(self, manual_server: Optional[str] = None):
        """
        Initialize the client.
        
        Args:
            manual_server: Optional manual server address (e.g., "192.168.4.2:8765")
                          If provided, skips mDNS discovery.
        """
        self.servers: Dict[str, GhostStreamServer] = {}
        self.preferred_server: Optional[str] = None
        self.zeroconf: Optional[Zeroconf] = None
        self.browser: Optional[ServiceBrowser] = None
        self._discovery_started = False
        self._callbacks: List[Callable[[str, GhostStreamServer], None]] = []
        
        # If manual server provided, add it directly
        if manual_server:
            host, port = manual_server.split(":")
            self.servers["manual"] = GhostStreamServer(
                name="manual",
                host=host,
                port=int(port)
            )
            self.preferred_server = "manual"
    
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
            return
        
        try:
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
            logger.info("Started GhostStream discovery")
        except Exception as e:
            logger.error(f"Failed to start discovery: {e}")
    
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
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{server.base_url}/api/health",
                    timeout=5.0
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
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{server.base_url}/api/capabilities",
                    timeout=10.0
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
            mode: "stream" for live HLS, "batch" for file output
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
        """
        server = server or self.get_server()
        if not server:
            logger.error("No GhostStream server available")
            return None
        
        request_body = {
            "source": source,
            "mode": mode,
            "output": {
                "format": format,
                "video_codec": video_codec,
                "audio_codec": audio_codec,
                "resolution": resolution,
                "bitrate": bitrate,
                "hw_accel": hw_accel
            },
            "start_time": start_time
        }
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{server.base_url}/api/transcode/start",
                    json=request_body,
                    timeout=30.0
                )
                
                if response.status_code == 200:
                    data = response.json()
                    return TranscodeJob(
                        job_id=data["job_id"],
                        status=TranscodeStatus(data["status"]),
                        progress=data.get("progress", 0),
                        stream_url=data.get("stream_url"),
                        download_url=data.get("download_url"),
                        hw_accel_used=data.get("hw_accel_used")
                    )
                else:
                    logger.error(f"Transcode request failed: {response.text}")
        except Exception as e:
            logger.error(f"Transcode request error: {e}")
        
        return None
    
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
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{server.base_url}/api/transcode/{job_id}/status",
                    timeout=10.0
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
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{server.base_url}/api/transcode/{job_id}/cancel",
                    timeout=10.0
                )
                return response.status_code == 200
        except Exception as e:
            logger.error(f"Cancel request error: {e}")
        
        return False
    
    async def wait_for_ready(
        self,
        job_id: str,
        timeout: float = 300,
        poll_interval: float = 1.0,
        server: Optional[GhostStreamServer] = None
    ) -> Optional[TranscodeJob]:
        """
        Wait for a job to be ready for streaming.
        
        For live transcoding (HLS), the job becomes ready quickly
        as segments are generated.
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
        manual_servers: Optional[List[str]] = None
    ):
        """
        Initialize the load balancer.
        
        Args:
            strategy: How to distribute jobs across servers
            manual_servers: List of server addresses (e.g., ["192.168.4.2:8765", "192.168.4.3:8765"])
        """
        self.strategy = strategy
        self.client = GhostStreamClient()
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
        import time
        
        for name, server in self.client.servers.items():
            try:
                async with httpx.AsyncClient() as http:
                    response = await http.get(
                        f"{server.base_url}/api/health",
                        timeout=5.0
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
        import random
        import time
        
        # Refresh stats if stale (>10 seconds)
        current_time = time.time()
        needs_refresh = any(
            current_time - s.last_health_check > 10
            for s in self.server_stats.values()
        )
        if needs_refresh:
            await self.refresh_stats()
        
        # Get healthy servers
        healthy_servers = [
            (name, self.client.servers[name])
            for name, stats in self.server_stats.items()
            if stats.is_healthy and name in self.client.servers
        ]
        
        if not healthy_servers:
            # Fallback to any available server
            if self.client.servers:
                return next(iter(self.client.servers.values()))
            return None
        
        if self.strategy == LoadBalanceStrategy.ROUND_ROBIN:
            self._round_robin_index = (self._round_robin_index + 1) % len(healthy_servers)
            return healthy_servers[self._round_robin_index][1]
        
        elif self.strategy == LoadBalanceStrategy.LEAST_BUSY:
            # Pick server with lowest (active_jobs + queued_jobs)
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
            logger.error("No GhostStream servers available")
            return None
        
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

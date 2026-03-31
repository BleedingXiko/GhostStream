"""Specter-owned GhostStream application runtime — fully gevent-native."""

from __future__ import annotations

import logging
import socket
import time
from pathlib import Path
from typing import Optional

from .config import get_config
from .discovery import GhostHubRegistration, GhostStreamService
from .jobs import JobManager, set_job_manager
from .security import (
    RegistrationAuthService,
    get_capability_service,
    get_node_identity,
    set_capability_service,
)
from .specter.core.lifecycle import Service
from .specter.core.manager import ServiceManager
from .network.websocket import (
    broadcast_progress,
    broadcast_status,
    get_websocket_manager,
)
from .network.server import NetworkIngressController

logger = logging.getLogger(__name__)


def _resolve_bind_host(host: str) -> str:
    if host != "0.0.0.0":
        return host
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        detected = sock.getsockname()[0]
        sock.close()
        return detected
    except Exception:
        return "127.0.0.1"


def determine_base_url() -> str:
    config = get_config()
    if config.server.advertised_url:
        return config.server.advertised_url.rstrip("/")
    host = _resolve_bind_host(config.server.host)
    return f"http://{host}:{config.server.port}"


class WebSocketService(Service):
    def __init__(self):
        super().__init__("websocket")
        self.manager = get_websocket_manager()

    def on_start(self) -> None:
        self.manager.start()

    def on_stop(self) -> None:
        self.manager.stop()


class JobService(Service):
    def __init__(self, base_url: str):
        super().__init__("jobs")
        self.base_url = base_url
        self.job_manager = JobManager(
            base_url=base_url,
            capability_service=get_capability_service(),
        )

    def on_start(self) -> None:
        set_job_manager(self.job_manager)
        self.job_manager.register_progress_callback(broadcast_progress)
        self.job_manager.register_status_callback(broadcast_status)
        self.job_manager.start()

    def on_stop(self) -> None:
        self.job_manager.stop()


class DiscoveryServiceRuntime(Service):
    def __init__(self):
        super().__init__("discovery")
        config = get_config()
        self.service = GhostStreamService(config.server.host, config.server.port)

    def on_start(self) -> None:
        config = get_config()
        if not config.mdns.enabled:
            return
        self.service.start()
        self.service.start_udp_responder()

    def on_stop(self) -> None:
        self.service.stop()


class RegistrationServiceRuntime(Service):
    def __init__(self, base_url: str):
        super().__init__("registration")
        config = get_config()
        identity = get_node_identity()
        self.auth_service = RegistrationAuthService(
            identity,
            shared_secret=config.security.registration_secret,
        )
        self.registration = GhostHubRegistration(
            ghosthub_url=config.ghosthub.url or "",
            port=config.server.port,
            callback_url=base_url,
            auth_service=self.auth_service,
        )

    def on_start(self) -> None:
        config = get_config()
        if not config.ghosthub.url or not config.ghosthub.auto_register:
            return
        import gevent
        gevent.spawn(
            self.registration.start_periodic_registration,
            interval_seconds=config.ghosthub.register_interval_seconds,
        )

    def on_stop(self) -> None:
        self.registration.stop()


class GhostStreamApplication:
    """Specter-owned GhostStream composition root.

    Uses ServiceManager as the sole lifecycle owner.
    Service start order:
      1. WebSocket manager
      2. Job manager
      3. Discovery (mDNS)
      4. GhostHub registration
      5. Network ingress (HTTP/HLS/WS)

    Shutdown is reverse order.
    """

    def __init__(self):
        self.started_at = time.time()
        self.base_url = determine_base_url()
        self.identity = get_node_identity()
        self.capability_service = get_capability_service()
        set_capability_service(self.capability_service)

        self.manager = ServiceManager("ghoststream")

        self.websocket_service = WebSocketService()
        self.job_service = JobService(self.base_url)
        self.discovery_service = DiscoveryServiceRuntime()
        self.registration_service = RegistrationServiceRuntime(self.base_url)
        self.ingress = NetworkIngressController()

        self.manager.register(self.websocket_service)
        self.manager.register(self.job_service)
        self.manager.register(self.discovery_service)
        self.manager.register(self.registration_service)
        self.manager.register(self.ingress)

    def start(self) -> None:
        self.started_at = time.time()
        Path(get_config().transcoding.temp_directory).mkdir(parents=True, exist_ok=True)
        set_runtime(self)
        self.manager.start()

    def stop(self) -> None:
        try:
            self.manager.stop()
        finally:
            set_runtime(None)


_runtime: Optional[GhostStreamApplication] = None


def get_runtime() -> Optional[GhostStreamApplication]:
    return _runtime


def set_runtime(runtime: Optional[GhostStreamApplication]) -> None:
    global _runtime
    _runtime = runtime


def create_runtime() -> GhostStreamApplication:
    runtime = GhostStreamApplication()
    set_runtime(runtime)
    return runtime

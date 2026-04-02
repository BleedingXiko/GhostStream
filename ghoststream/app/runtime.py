"""Specter-first GhostStream runtime composition root."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

import gevent

from ..contracts.websocket import MESSAGE_TYPE_PROGRESS, MESSAGE_TYPE_STATUS_CHANGE
from ..specter.core.lifecycle import Service
from ..specter.core.manager import ServiceManager
from ..specter.core.registry import registry
from ..server.domain.transcoding.models import TranscodeProgress
from ..server.services.client_presence import ClientPresenceService
from ..server.services.websocket import WebSocketManager
from .bootstrap import determine_base_url
from .factories import (
    create_capability_service,
    create_discovery_service,
    create_ghosthub_registration,
    create_http_controllers,
    create_job_manager,
    create_network_ingress,
    create_node_identity,
    create_registration_auth_service,
    create_websocket_manager,
)
from .registry_keys import (
    APP_CONFIG,
    CAPABILITY_SERVICE,
    CLIENT_PRESENCE,
    JOB_MANAGER,
    NODE_IDENTITY,
    RUNTIME_CONTEXT,
    WEBSOCKET_MANAGER,
)

logger = logging.getLogger(__name__)


@dataclass
class RuntimeContext:
    config: object
    base_url: str
    started_at: float


class RuntimeBindingsService(Service):
    def __init__(
        self,
        *,
        runtime_context,
        config,
        identity,
        capability_service,
        websocket_manager,
        job_manager,
        client_presence,
    ):
        super().__init__("runtime_bindings")
        self.runtime_context = runtime_context
        self.config = config
        self.identity = identity
        self.capability_service = capability_service
        self.websocket_manager = websocket_manager
        self.job_manager = job_manager
        self.client_presence = client_presence

    def on_start(self) -> None:
        registry.provide(APP_CONFIG, self.config, owner=self, replace=True)
        registry.provide(RUNTIME_CONTEXT, self.runtime_context, owner=self, replace=True)
        registry.provide(NODE_IDENTITY, self.identity, owner=self, replace=True)
        registry.provide(CAPABILITY_SERVICE, self.capability_service, owner=self, replace=True)
        registry.provide(WEBSOCKET_MANAGER, self.websocket_manager, owner=self, replace=True)
        registry.provide(JOB_MANAGER, self.job_manager, owner=self, replace=True)
        registry.provide(CLIENT_PRESENCE, self.client_presence, owner=self, replace=True)


class WebSocketService(Service):
    def __init__(self, websocket_manager: WebSocketManager):
        super().__init__("websocket")
        self.websocket_manager = websocket_manager

    def on_start(self) -> None:
        self.websocket_manager.start()

    def on_stop(self) -> None:
        self.websocket_manager.stop()


class JobService(Service):
    def __init__(self, *, job_manager, websocket_manager: WebSocketManager):
        super().__init__("jobs")
        self.job_manager = job_manager
        self.websocket_manager = websocket_manager

    def on_start(self) -> None:
        self.job_manager.register_progress_callback(self._broadcast_progress)
        self.job_manager.register_status_callback(self._broadcast_status)
        self.job_manager.start()

    def on_stop(self) -> None:
        self.job_manager.stop()

    def _broadcast_progress(self, job_id: str, progress: TranscodeProgress) -> None:
        self.websocket_manager.broadcast(
            {
                "type": MESSAGE_TYPE_PROGRESS,
                "job_id": job_id,
                "data": {
                    "progress": progress.percent,
                    "frame": progress.frame,
                    "fps": progress.fps,
                    "time": progress.time,
                    "speed": progress.speed,
                },
            },
            job_id=job_id,
        )

    def _broadcast_status(self, job_id: str, status) -> None:
        self.websocket_manager.broadcast(
            {
                "type": MESSAGE_TYPE_STATUS_CHANGE,
                "job_id": job_id,
                "data": {"status": status.value},
            },
            job_id=job_id,
        )


class DiscoveryServiceRuntime(Service):
    def __init__(self, *, config, discovery_service):
        super().__init__("discovery")
        self.config = config
        self.discovery_service = discovery_service

    def on_start(self) -> None:
        if not self.config.mdns.enabled:
            return
        self.discovery_service.start()
        self.discovery_service.start_udp_responder()

    def on_stop(self) -> None:
        self.discovery_service.stop()


class RegistrationServiceRuntime(Service):
    def __init__(self, *, config, registration):
        super().__init__("registration")
        self.config = config
        self.registration = registration

    def on_start(self) -> None:
        ghosthub_url = os.environ.get("GHOSTHUB_URL") or self.config.ghosthub.url
        if not ghosthub_url or not self.config.ghosthub.auto_register:
            return
        gevent.spawn(
            self.registration.start_periodic_registration,
            interval_seconds=self.config.ghosthub.register_interval_seconds,
        )

    def on_stop(self) -> None:
        self.registration.stop()


class GhostStreamRuntime:
    """Single owned runtime graph for GhostStream."""

    def __init__(self, config):
        self.config = config
        self.base_url = determine_base_url(config)
        self.identity = create_node_identity(config)
        self.capability_service = create_capability_service(config, self.identity)
        self.websocket_manager = create_websocket_manager(self.capability_service)
        self.client_presence = ClientPresenceService()
        self.job_manager = create_job_manager(
            config,
            base_url=self.base_url,
            capability_service=self.capability_service,
        )
        self.runtime_context = RuntimeContext(
            config=config,
            base_url=self.base_url,
            started_at=time.time(),
        )

        discovery_service = create_discovery_service(config)
        auth_service = create_registration_auth_service(config, self.identity)
        registration = create_ghosthub_registration(
            config,
            base_url=self.base_url,
            auth_service=auth_service,
        )
        http_controllers = create_http_controllers()
        ingress = create_network_ingress(
            config,
            websocket_manager=self.websocket_manager,
            controllers=http_controllers,
        )

        self.manager = ServiceManager()
        self.manager.register_service(
            RuntimeBindingsService(
                runtime_context=self.runtime_context,
                config=self.config,
                identity=self.identity,
                capability_service=self.capability_service,
                websocket_manager=self.websocket_manager,
                job_manager=self.job_manager,
                client_presence=self.client_presence,
            )
        )
        self.manager.register_service(WebSocketService(self.websocket_manager))
        self.manager.register_service(
            JobService(job_manager=self.job_manager, websocket_manager=self.websocket_manager)
        )
        self.manager.register_service(
            DiscoveryServiceRuntime(config=self.config, discovery_service=discovery_service)
        )
        self.manager.register_service(
            RegistrationServiceRuntime(config=self.config, registration=registration)
        )
        self.manager.register_service(ingress)

    def start(self) -> None:
        self.runtime_context.started_at = time.time()
        Path(self.config.transcoding.temp_directory).mkdir(parents=True, exist_ok=True)
        self.manager.boot()
        missing_services = [
            service_name
            for service_name in ("runtime_bindings", "websocket", "jobs", "http-ingress")
            if not registry.has(service_name)
        ]
        if missing_services:
            raise RuntimeError(
                "GhostStream runtime failed to start required services: "
                + ", ".join(missing_services)
            )

    def stop(self) -> None:
        self.manager.shutdown()

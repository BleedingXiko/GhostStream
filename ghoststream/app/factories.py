"""Concrete runtime factories owned by the GhostStream composition root."""

from __future__ import annotations

import shutil
from pathlib import Path

from ..hardware import get_capabilities
from ..server.controllers.api import APIController
from ..server.controllers.ops import OperationsController
from ..server.controllers.streams import StreamsController
from ..server.controllers.websocket import GhostStreamIngressController
from ..server.domain.security.capabilities import CapabilityService
from ..server.domain.security.registration import RegistrationAuthService
from ..server.domain.streaming.hls import HLSConfig, HLSPlaylistGenerator
from ..server.infra.security.node_identity_store import NodeIdentityStore
from ..server.infra.ffmpeg.adaptive import HardwareProfiler
from ..server.infra.ffmpeg.commands import CommandBuilder
from ..server.infra.ffmpeg.encoders import EncoderSelector
from ..server.infra.ffmpeg.engine import TranscodeEngine
from ..server.infra.ffmpeg.error_classifier import ErrorClassifier
from ..server.infra.ffmpeg.ffmpeg_runner import FFmpegRunner, StallConfig
from ..server.infra.ffmpeg.filters import FilterBuilder
from ..server.infra.ffmpeg.job_context import JobRegistry
from ..server.infra.ffmpeg.probe import MediaProbe
from ..server.services.discovery import GhostHubRegistrationService, GhostStreamDiscoveryService
from ..server.services.jobs import JobManager
from ..server.services.websocket import WebSocketManager


def create_node_identity(config):
    state_dir = Path(config.security.state_directory).expanduser()
    return NodeIdentityStore(state_dir).load_or_create()


def create_capability_service(config, identity):
    return CapabilityService(
        identity.secret_bytes(),
        node_id=identity.node_id,
        default_ttl_seconds=config.security.job_token_ttl_seconds,
    )


def create_websocket_manager(capability_service) -> WebSocketManager:
    return WebSocketManager(capability_service=capability_service)


def create_http_controllers():
    return (
        OperationsController(),
        APIController(),
        StreamsController(),
    )


def create_job_manager(config, *, base_url: str, capability_service):
    engine = create_transcode_engine(config)
    return JobManager(
        config=config,
        engine=engine,
        base_url=base_url,
        capability_service=capability_service,
    )


def create_transcode_engine(config):
    capabilities = get_capabilities(
        config.transcoding.ffmpeg_path,
        config.transcoding.max_concurrent_jobs,
    )
    ffmpeg_path = config.transcoding.ffmpeg_path
    if ffmpeg_path == "auto":
        ffmpeg_path = shutil.which("ffmpeg") or "ffmpeg"
    probe = MediaProbe()
    filter_builder = FilterBuilder(ffmpeg_path)
    encoder_selector = EncoderSelector(capabilities, config.hardware)
    command_builder = CommandBuilder(
        ffmpeg_path,
        encoder_selector,
        filter_builder,
        config.transcoding,
        config.hardware,
    )
    hardware_profiler = HardwareProfiler(capabilities)
    error_classifier = ErrorClassifier()
    ffmpeg_runner = FFmpegRunner(
        stall_config=StallConfig(
            base_timeout=max(120.0, config.transcoding.stall_timeout),
            timeout_per_segment=10.0,
        ),
    )
    job_registry = JobRegistry()
    hls_generator = HLSPlaylistGenerator(
        HLSConfig(segment_duration=config.transcoding.segment_duration)
    )
    return TranscodeEngine(
        config=config,
        capabilities=capabilities,
        probe=probe,
        filter_builder=filter_builder,
        encoder_selector=encoder_selector,
        command_builder=command_builder,
        hardware_profiler=hardware_profiler,
        error_classifier=error_classifier,
        ffmpeg_runner=ffmpeg_runner,
        job_registry=job_registry,
        hls_generator=hls_generator,
    )


def create_discovery_service(config):
    return GhostStreamDiscoveryService(
        host=config.server.host,
        port=config.server.port,
        service_name=config.mdns.service_name,
        capabilities_factory=lambda: get_capabilities(
            ffmpeg_path=config.transcoding.ffmpeg_path,
            max_concurrent_jobs=config.transcoding.max_concurrent_jobs,
        ),
    )


def create_registration_auth_service(config, identity):
    return RegistrationAuthService(
        identity,
        shared_secret=config.security.registration_secret,
    )


def create_ghosthub_registration(config, *, base_url: str, auth_service):
    return GhostHubRegistrationService(
        ghosthub_url=config.ghosthub.url or "",
        port=config.server.port,
        callback_url=base_url,
        service_name=config.mdns.service_name,
        advertised_url=config.server.advertised_url,
        capabilities_factory=lambda: get_capabilities(
            ffmpeg_path=config.transcoding.ffmpeg_path,
            max_concurrent_jobs=config.transcoding.max_concurrent_jobs,
        ),
        auth_service=auth_service,
    )


def create_network_ingress(config, *, websocket_manager, controllers):
    return GhostStreamIngressController(
        host=config.server.host,
        port=config.server.port,
        websocket_manager=websocket_manager,
        controllers=controllers,
    )

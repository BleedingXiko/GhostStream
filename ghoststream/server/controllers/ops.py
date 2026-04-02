"""Specter controller for GhostStream operational endpoints."""

from __future__ import annotations

import os
import time

from flask import jsonify

from ... import __version__
from ...app.registry_keys import APP_CONFIG, CLIENT_PRESENCE, JOB_MANAGER, RUNTIME_CONTEXT, WEBSOCKET_MANAGER
from ...contracts.api import CapabilitiesResponse, HealthResponse, StatsResponse
from ...hardware import get_capabilities
from ...specter.core.controller import Controller
from ...specter.core.registry import registry
from .websocket import track_http_client_header


class OperationsController(Controller):
    def __init__(self) -> None:
        super().__init__("ghoststream_ops")

    @property
    def config(self):
        return registry.require(APP_CONFIG)

    @property
    def job_manager(self):
        return registry.require(JOB_MANAGER)

    @property
    def runtime_context(self):
        return registry.require(RUNTIME_CONTEXT)

    @property
    def websocket_manager(self):
        return registry.require(WEBSOCKET_MANAGER)

    @property
    def client_presence(self):
        return registry.require(CLIENT_PRESENCE)

    def build_routes(self, router) -> None:
        @router.route("/api/health", methods=["GET"], endpoint="health_check")
        def health_check():
            track_http_client_header()
            body = HealthResponse(
                status="healthy",
                version=__version__,
                uptime_seconds=time.time() - self.runtime_context.started_at,
                current_jobs=self.job_manager.get_active_count(),
                queued_jobs=self.job_manager.get_queue_length(),
            )
            return jsonify(body.model_dump())

        @router.route("/api/health/detailed", methods=["GET"], endpoint="detailed_health_check")
        def detailed_health_check():
            track_http_client_header()
            import psutil

            cpu_percent = psutil.cpu_percent(interval=0.1)
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage(self.config.transcoding.temp_directory)

            status = "healthy"
            checks = {}

            checks["cpu"] = {
                "status": "healthy" if cpu_percent < 90 else "degraded",
                "usage_percent": cpu_percent,
            }
            if cpu_percent >= 95:
                status = "unhealthy"
            elif cpu_percent >= 90:
                status = "degraded"

            checks["memory"] = {
                "status": "healthy" if memory.percent < 85 else "degraded",
                "usage_percent": memory.percent,
                "available_mb": memory.available // (1024 * 1024),
            }
            if memory.percent >= 95:
                status = "unhealthy"
            elif memory.percent >= 85 and status == "healthy":
                status = "degraded"

            checks["disk"] = {
                "status": "healthy" if disk.percent < 85 else "degraded",
                "usage_percent": disk.percent,
                "free_gb": disk.free // (1024 * 1024 * 1024),
            }
            if disk.percent >= 95:
                status = "unhealthy"
            elif disk.percent >= 85 and status == "healthy":
                status = "degraded"

            queue_length = self.job_manager.get_queue_length()
            max_queue = self.config.transcoding.max_queue_size
            queue_percent = (queue_length / max_queue) * 100 if max_queue > 0 else 0
            checks["job_queue"] = {
                "status": "healthy" if queue_percent < 80 else "degraded",
                "current": queue_length,
                "max": max_queue,
                "usage_percent": queue_percent,
            }
            if queue_percent >= 95 and status == "healthy":
                status = "degraded"

            checks["active_jobs"] = {
                "status": "healthy",
                "count": self.job_manager.get_active_count(),
                "max_concurrent": self.config.transcoding.max_concurrent_jobs,
            }

            http_status = 200 if status == "healthy" else (503 if status == "unhealthy" else 200)

            return jsonify(
                {
                    "status": status,
                    "version": __version__,
                    "environment": os.environ.get("GHOSTSTREAM_ENV", "development"),
                    "uptime_seconds": time.time() - self.runtime_context.started_at,
                    "checks": checks,
                    "timestamp": time.time(),
                }
            ), http_status

        @router.route("/api/ready", methods=["GET"], endpoint="readiness_check")
        def readiness_check():
            track_http_client_header()
            queue_length = self.job_manager.get_queue_length()
            max_queue = self.config.transcoding.max_queue_size

            if queue_length >= max_queue:
                return jsonify({"ready": False, "reason": "Queue full"}), 503

            return jsonify({"ready": True})

        @router.route("/api/live", methods=["GET"], endpoint="liveness_check")
        def liveness_check():
            track_http_client_header()
            return jsonify({"alive": True, "timestamp": time.time()})

        @router.route("/api/capabilities", methods=["GET"], endpoint="get_capabilities")
        def get_capabilities_endpoint():
            track_http_client_header()
            capabilities = get_capabilities(
                self.config.transcoding.ffmpeg_path,
                self.config.transcoding.max_concurrent_jobs,
                force_refresh=False,
            )
            body = CapabilitiesResponse(**capabilities.to_dict())
            return jsonify(body.model_dump())

        @router.route("/api/stats", methods=["GET"], endpoint="get_stats")
        def get_stats():
            track_http_client_header()
            stats = self.job_manager.stats
            body = StatsResponse(
                total_jobs_processed=stats.total_jobs_processed,
                successful_jobs=stats.successful_jobs,
                failed_jobs=stats.failed_jobs,
                cancelled_jobs=stats.cancelled_jobs,
                current_queue_length=self.job_manager.get_queue_length(),
                active_jobs=self.job_manager.get_active_count(),
                average_transcode_speed=stats.average_transcode_speed,
                total_bytes_processed=stats.total_bytes_processed,
                uptime_seconds=stats.uptime_seconds,
                hw_accel_usage=stats.hw_accel_usage,
            )
            return jsonify(body.model_dump())

        @router.route("/api/cleanup/stats", methods=["GET"], endpoint="get_cleanup_stats")
        def get_cleanup_stats():
            track_http_client_header()
            return jsonify(self.job_manager.get_cleanup_stats())

        @router.route("/api/cleanup/run", methods=["POST"], endpoint="run_cleanup")
        def run_cleanup():
            track_http_client_header()
            return jsonify(self.job_manager.run_cleanup())

        @router.route("/api/streams/shared", methods=["GET"], endpoint="get_shared_streams")
        def get_shared_streams():
            track_http_client_header()
            return jsonify(self.job_manager.get_shared_stream_stats())

        @router.route("/api/clients", methods=["GET"], endpoint="get_ws_clients")
        def get_ws_clients():
            track_http_client_header()
            clients = self.websocket_manager.get_websocket_clients()
            seen_names = {client.get("client") for client in clients}
            for name in self.client_presence.get_active_names():
                if name not in seen_names:
                    clients.append({"id": None, "client": name, "transport": "http"})
                    seen_names.add(name)
            for name in self.job_manager.get_active_client_names():
                if name not in seen_names:
                    clients.append({"id": None, "client": name, "transport": "job"})
                    seen_names.add(name)
            return jsonify(clients)

        @router.route("/health", methods=["GET"], endpoint="compat_health")
        def compat_health():
            return health_check()

        @router.route("/capabilities", methods=["GET"], endpoint="compat_capabilities")
        def compat_capabilities():
            return get_capabilities_endpoint()

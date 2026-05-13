"""Specter controller for GhostStream transcode lifecycle endpoints."""

from __future__ import annotations

from flask import jsonify, request
from pydantic import ValidationError
from specter.core.controller import Controller
from specter.core.registry import registry

from ...app.registry_keys import JOB_MANAGER
from ...contracts.api import JobStatus, TranscodeRequest
from .security import require_control_capability
from .websocket import track_http_client_header


class APIController(Controller):
    def __init__(self) -> None:
        super().__init__("ghoststream_api")

    @property
    def job_manager(self):
        return registry.require(JOB_MANAGER)

    def build_routes(self, router) -> None:
        @router.route("/api/transcode/start", methods=["POST"], endpoint="start_transcode")
        def start_transcode():
            client_name = track_http_client_header()
            raw = request.get_json(silent=True)
            if raw is None:
                return jsonify({"detail": "Invalid JSON body"}), 400

            try:
                req = TranscodeRequest(**raw)
            except ValidationError as exc:
                return jsonify({"detail": exc.errors()}), 422

            if not req.source:
                return jsonify({"detail": "Source URL is required"}), 400

            job = self.job_manager.create_job(req, req.session_id, client_name=client_name)
            resp = self.job_manager.build_transcode_response(job)
            return jsonify(resp.model_dump())

        @router.route("/api/transcode/<job_id>/status", methods=["GET"], endpoint="get_job_status")
        def get_job_status(job_id: str):
            client_name = track_http_client_header()
            self.job_manager.note_job_client(job_id, client_name)
            require_control_capability(job_id)

            job = self.job_manager.get_job(job_id)
            if not job:
                return jsonify({"detail": "Job not found"}), 404

            resp = self.job_manager.build_status_response(job)
            return jsonify(resp.model_dump(mode="json"))

        @router.route("/api/transcode/<job_id>/cancel", methods=["POST"], endpoint="cancel_job")
        def cancel_job(job_id: str):
            client_name = track_http_client_header()
            self.job_manager.note_job_client(job_id, client_name)
            require_control_capability(job_id)

            success = self.job_manager.cancel_job(job_id)
            if not success:
                return jsonify({"detail": "Job cannot be cancelled"}), 400

            return jsonify({"status": "cancelled", "job_id": job_id})

        @router.route("/api/transcode/<job_id>/stream", methods=["GET"], endpoint="get_stream_info")
        def get_stream_info(job_id: str):
            client_name = track_http_client_header()
            self.job_manager.note_job_client(job_id, client_name)
            require_control_capability(job_id)

            job = self.job_manager.get_job(job_id)
            if not job:
                return jsonify({"detail": "Job not found"}), 404

            if job.status != JobStatus.READY and job.status != JobStatus.PROCESSING:
                return jsonify(
                    {"detail": f"Job is not ready for streaming: {job.status.value}"}
                ), 400

            return jsonify(
                {
                    "job_id": job_id,
                    "stream_url": self.job_manager.build_stream_url(job),
                    "status": job.status.value,
                }
            )

        @router.route("/api/transcode/<job_id>", methods=["DELETE"], endpoint="delete_job")
        def delete_job(job_id: str):
            client_name = track_http_client_header()
            self.job_manager.note_job_client(job_id, client_name)
            require_control_capability(job_id)

            job = self.job_manager.get_job(job_id, touch=False)
            if not job:
                return jsonify({"detail": "Job not found"}), 404

            if job.status in [JobStatus.QUEUED, JobStatus.PROCESSING]:
                self.job_manager.cancel_job(job_id)

            self.job_manager.remove_job(job_id)
            return jsonify({"status": "deleted", "job_id": job_id})

        @router.route("/api/transcode/<job_id>/leave", methods=["POST"], endpoint="leave_stream")
        def leave_stream(job_id: str):
            client_name = track_http_client_header()
            self.job_manager.note_job_client(job_id, client_name)
            require_control_capability(job_id)

            job = self.job_manager.get_job(job_id, touch=False)
            if not job:
                return jsonify({"detail": "Job not found"}), 404

            raw = request.get_json(silent=True) or {}
            session_id = raw.get("session_id")

            self.job_manager.leave_stream(job_id, session_id)

            return jsonify(
                {
                    "job_id": job_id,
                    "viewers_remaining": job.viewer_count,
                    "is_shared": job.is_shared,
                }
            )

        @router.route("/transcode", methods=["POST"], endpoint="compat_transcode")
        def compat_transcode():
            return start_transcode()

        @router.route("/transcode/<job_id>", methods=["GET"], endpoint="compat_job_status")
        def compat_job_status(job_id: str):
            return get_job_status(job_id)

        @router.route("/transcode/<job_id>", methods=["DELETE"], endpoint="compat_cancel_job")
        def compat_cancel_job(job_id: str):
            return cancel_job(job_id)

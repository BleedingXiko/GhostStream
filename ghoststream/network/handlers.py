"""
Flask route handlers for GhostStream — Specter-native.

Every handler is a plain function registered on the Flask app.
No aiohttp, no async/await.
"""

import logging
import os
import time
import gevent
from pathlib import Path
from typing import Optional

from flask import Flask, request, jsonify, make_response, send_file, Response
from pydantic import ValidationError

from .. import __version__
from ..config import get_config
from ..hardware import get_capabilities
from ..jobs import get_job_manager
from ..models import (
    CapabilitiesResponse,
    HealthResponse,
    JobStatus,
    StatsResponse,
    TranscodeRequest,
)
from ..runtime import get_runtime
from .security import (
    append_token_to_playlist,
    require_control_capability,
    require_stream_capability,
)

logger = logging.getLogger(__name__)

PLAYLIST_STALE_THRESHOLD = 30.0


# ---------------------------------------------------------------------------
# Health routes
# ---------------------------------------------------------------------------

def _get_start_time() -> float:
    runtime = get_runtime()
    if runtime is not None:
        return runtime.started_at
    return 0.0


def health_check():
    job_manager = get_job_manager()
    body = HealthResponse(
        status="healthy",
        version=__version__,
        uptime_seconds=time.time() - _get_start_time(),
        current_jobs=job_manager.get_active_count(),
        queued_jobs=job_manager.get_queue_length(),
    )
    return jsonify(body.model_dump())


def detailed_health_check():
    import psutil

    job_manager = get_job_manager()
    config = get_config()

    cpu_percent = psutil.cpu_percent(interval=0.1)
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage(config.transcoding.temp_directory)

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

    queue_length = job_manager.get_queue_length()
    max_queue = config.transcoding.max_queue_size
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
        "count": job_manager.get_active_count(),
        "max_concurrent": config.transcoding.max_concurrent_jobs,
    }

    http_status = 200 if status == "healthy" else (503 if status == "unhealthy" else 200)

    return jsonify({
        "status": status,
        "version": __version__,
        "environment": os.environ.get("GHOSTSTREAM_ENV", "development"),
        "uptime_seconds": time.time() - _get_start_time(),
        "checks": checks,
        "timestamp": time.time(),
    }), http_status


def readiness_check():
    job_manager = get_job_manager()
    config = get_config()
    queue_length = job_manager.get_queue_length()
    max_queue = config.transcoding.max_queue_size

    if queue_length >= max_queue:
        return jsonify({"ready": False, "reason": "Queue full"}), 503

    return jsonify({"ready": True})


def liveness_check():
    return jsonify({"alive": True, "timestamp": time.time()})


def get_capabilities_endpoint():
    config = get_config()
    capabilities = get_capabilities(
        config.transcoding.ffmpeg_path,
        config.transcoding.max_concurrent_jobs,
        force_refresh=False,
    )
    body = CapabilitiesResponse(**capabilities.to_dict())
    return jsonify(body.model_dump())


def get_stats():
    job_manager = get_job_manager()
    stats = job_manager.stats
    body = StatsResponse(
        total_jobs_processed=stats.total_jobs_processed,
        successful_jobs=stats.successful_jobs,
        failed_jobs=stats.failed_jobs,
        cancelled_jobs=stats.cancelled_jobs,
        current_queue_length=job_manager.get_queue_length(),
        active_jobs=job_manager.get_active_count(),
        average_transcode_speed=stats.average_transcode_speed,
        total_bytes_processed=stats.total_bytes_processed,
        uptime_seconds=stats.uptime_seconds,
        hw_accel_usage=stats.hw_accel_usage,
    )
    return jsonify(body.model_dump())


# ---------------------------------------------------------------------------
# Transcode routes
# ---------------------------------------------------------------------------

def start_transcode():
    job_manager = get_job_manager()

    raw = request.get_json(silent=True)
    if raw is None:
        return jsonify({"detail": "Invalid JSON body"}), 400

    try:
        req = TranscodeRequest(**raw)
    except ValidationError as exc:
        return jsonify({"detail": exc.errors()}), 422

    if not req.source:
        return jsonify({"detail": "Source URL is required"}), 400

    job = job_manager.create_job(req, req.session_id)
    resp = job_manager.build_transcode_response(job)
    return jsonify(resp.model_dump())


def get_job_status(job_id: str):
    job_manager = get_job_manager()
    require_control_capability(job_id)

    job = job_manager.get_job(job_id)
    if not job:
        return jsonify({"detail": "Job not found"}), 404

    resp = job_manager.build_status_response(job)
    return jsonify(resp.model_dump(mode="json"))


def cancel_job(job_id: str):
    job_manager = get_job_manager()
    require_control_capability(job_id)

    success = job_manager.cancel_job(job_id)
    if not success:
        return jsonify({"detail": "Job cannot be cancelled"}), 400

    return jsonify({"status": "cancelled", "job_id": job_id})


def get_stream_info(job_id: str):
    job_manager = get_job_manager()
    require_control_capability(job_id)

    job = job_manager.get_job(job_id)
    if not job:
        return jsonify({"detail": "Job not found"}), 404

    if job.status != JobStatus.READY and job.status != JobStatus.PROCESSING:
        return jsonify(
            {"detail": f"Job is not ready for streaming: {job.status.value}"}
        ), 400

    return jsonify({
        "job_id": job_id,
        "stream_url": job_manager.build_stream_url(job),
        "status": job.status.value,
    })


def delete_job(job_id: str):
    job_manager = get_job_manager()
    require_control_capability(job_id)

    job = job_manager.get_job(job_id, touch=False)
    if not job:
        return jsonify({"detail": "Job not found"}), 404

    if job.status in [JobStatus.QUEUED, JobStatus.PROCESSING]:
        job_manager.cancel_job(job_id)

    job_manager.remove_job(job_id)
    return jsonify({"status": "deleted", "job_id": job_id})


def get_cleanup_stats():
    job_manager = get_job_manager()
    return jsonify(job_manager.get_cleanup_stats())


def run_cleanup():
    job_manager = get_job_manager()
    cleaned = job_manager._cleanup_stale_jobs()
    orphaned = job_manager._cleanup_orphaned_dirs()
    return jsonify(
        {"stale_jobs_cleaned": cleaned, "orphaned_dirs_cleaned": orphaned}
    )


def get_shared_streams():
    job_manager = get_job_manager()
    return jsonify(job_manager.get_shared_stream_stats())


def leave_stream(job_id: str):
    job_manager = get_job_manager()
    require_control_capability(job_id)

    job = job_manager.get_job(job_id, touch=False)
    if not job:
        return jsonify({"detail": "Job not found"}), 404

    raw = request.get_json(silent=True) or {}
    session_id = raw.get("session_id")

    job_manager.leave_stream(job_id, session_id)

    return jsonify({
        "job_id": job_id,
        "viewers_remaining": job.viewer_count,
        "is_shared": job.is_shared,
    })


# ---------------------------------------------------------------------------
# Stream / HLS serving
# ---------------------------------------------------------------------------

def _inject_endlist_if_needed(content: str, job_status: JobStatus) -> str:
    if job_status == JobStatus.READY:
        if "#EXT-X-ENDLIST" not in content:
            content = content.rstrip() + "\n#EXT-X-ENDLIST\n"
    return content


def _check_playlist_freshness(file_path: Path, job_status: JobStatus) -> tuple:
    if job_status != JobStatus.PROCESSING:
        return True, 0.0
    try:
        mtime = file_path.stat().st_mtime
        staleness = time.time() - mtime
        return staleness < PLAYLIST_STALE_THRESHOLD, staleness
    except Exception:
        return True, 0.0


def stream_file(job_id: str, filename: str):
    job_manager = get_job_manager()
    stream_token = require_stream_capability(job_id)

    job_manager.touch_job(job_id)

    config = get_config()
    temp_dir = Path(config.transcoding.temp_directory)

    # Security: prevent path traversal
    if ".." in filename or filename.startswith("/") or "\\" in filename:
        return jsonify({"detail": "Invalid filename"}), 400

    file_path = temp_dir / job_id / filename

    try:
        file_path = file_path.resolve()
        job_dir = (temp_dir / job_id).resolve()
        if not str(file_path).startswith(str(job_dir)):
            return jsonify({"detail": "Access denied"}), 403
    except (ValueError, OSError):
        return jsonify({"detail": "Invalid path"}), 400

    # Wait for playlist creation for slow-starting transcodes
    if filename.endswith(".m3u8") and not file_path.exists():
        job = job_manager.get_job(job_id, touch=False)
        if job and job.status in (JobStatus.PROCESSING, JobStatus.QUEUED):
            for i in range(60):
                gevent.sleep(0.5)
                if file_path.exists():
                    break
                if i % 10 == 9:
                    job = job_manager.get_job(job_id, touch=False)
                    if not job or job.status == JobStatus.ERROR:
                        break

    if not file_path.exists():
        return jsonify({"detail": "Stream file not found"}), 404

    # Playlist handling
    if filename.endswith(".m3u8"):
        media_type = "application/vnd.apple.mpegurl"
        job = job_manager.get_job(job_id, touch=False)
        job_status = job.status if job else JobStatus.READY

        is_fresh, staleness = _check_playlist_freshness(file_path, job_status)
        if not is_fresh and job and job_status == JobStatus.PROCESSING:
            logger.warning(
                f"[Stream] Playlist stale for {staleness:.0f}s, attempting restart for job {job_id}"
            )
            new_job = job_manager.restart_stale_stream(job_id)
            if new_job:
                new_url = job_manager.build_stream_url(new_job) or f"/stream/{new_job.id}/{filename}"
                logger.info(f"[Stream] Redirecting to restarted stream: {new_url}")
                return Response("", status=307, headers={"Location": new_url})

            content = file_path.read_text()
            content = _inject_endlist_if_needed(content, job_status)
            content = append_token_to_playlist(content, stream_token)
            return Response(
                content,
                content_type=media_type,
                headers={
                    "Accept-Ranges": "bytes",
                    "Cache-Control": "no-cache",
                    "X-Playlist-Stale": "true",
                    "X-Staleness-Seconds": str(int(staleness)),
                },
            )

        content = file_path.read_text()
        content = _inject_endlist_if_needed(content, job_status)
        content = append_token_to_playlist(content, stream_token)
        return Response(
            content,
            content_type=media_type,
            headers={"Accept-Ranges": "bytes", "Cache-Control": "no-cache"},
        )

    # Binary segment / media files
    if filename.endswith(".ts"):
        media_type = "video/mp2t"
    elif filename.endswith(".mp4"):
        media_type = "video/mp4"
    else:
        media_type = "application/octet-stream"

    file_size = file_path.stat().st_size
    range_header = request.headers.get("Range")

    if range_header:
        range_match = range_header.replace("bytes=", "").split("-")
        start = int(range_match[0]) if range_match[0] else 0
        end = int(range_match[1]) if range_match[1] else file_size - 1

        if start >= file_size:
            return jsonify({"detail": "Range not satisfiable"}), 416

        end = min(end, file_size - 1)
        content_length = end - start + 1

        def generate_range():
            with open(file_path, "rb") as f:
                f.seek(start)
                remaining = content_length
                while remaining > 0:
                    chunk_size = min(8192, remaining)
                    data = f.read(chunk_size)
                    if not data:
                        break
                    remaining -= len(data)
                    yield data

        return Response(
            generate_range(),
            status=206,
            content_type=media_type,
            headers={
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(content_length),
            },
        )

    return send_file(str(file_path), mimetype=media_type)


def download_file(job_id: str):
    job_manager = get_job_manager()
    require_stream_capability(job_id)

    job = job_manager.get_job(job_id)
    if not job:
        return jsonify({"detail": "Job not found"}), 404

    if job.status != JobStatus.READY:
        return jsonify({"detail": "Job is not ready for download"}), 400

    if not job.output_path or not Path(job.output_path).exists():
        return jsonify({"detail": "Output file not found"}), 404

    return send_file(
        job.output_path,
        as_attachment=True,
        download_name=Path(job.output_path).name,
    )


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------

def register_routes(app: Flask) -> None:
    """Register all GhostStream HTTP routes on the Flask application."""
    # Health
    app.add_url_rule("/api/health", "health_check", health_check, methods=["GET"])
    app.add_url_rule("/api/health/detailed", "detailed_health_check", detailed_health_check, methods=["GET"])
    app.add_url_rule("/api/ready", "readiness_check", readiness_check, methods=["GET"])
    app.add_url_rule("/api/live", "liveness_check", liveness_check, methods=["GET"])
    app.add_url_rule("/api/capabilities", "get_capabilities", get_capabilities_endpoint, methods=["GET"])
    app.add_url_rule("/api/stats", "get_stats", get_stats, methods=["GET"])

    # Transcode
    app.add_url_rule("/api/transcode/start", "start_transcode", start_transcode, methods=["POST"])
    app.add_url_rule("/api/transcode/<job_id>/status", "get_job_status", get_job_status, methods=["GET"])
    app.add_url_rule("/api/transcode/<job_id>/cancel", "cancel_job", cancel_job, methods=["POST"])
    app.add_url_rule("/api/transcode/<job_id>/stream", "get_stream_info", get_stream_info, methods=["GET"])
    app.add_url_rule("/api/transcode/<job_id>", "delete_job", delete_job, methods=["DELETE"])
    app.add_url_rule("/api/transcode/<job_id>/leave", "leave_stream", leave_stream, methods=["POST"])

    # Cleanup
    app.add_url_rule("/api/cleanup/stats", "get_cleanup_stats", get_cleanup_stats, methods=["GET"])
    app.add_url_rule("/api/cleanup/run", "run_cleanup", run_cleanup, methods=["POST"])

    # Shared streams
    app.add_url_rule("/api/streams/shared", "get_shared_streams", get_shared_streams, methods=["GET"])

    # Stream / HLS serving
    app.add_url_rule("/stream/<job_id>/<path:filename>", "stream_file", stream_file, methods=["GET"])
    app.add_url_rule("/download/<job_id>", "download_file", download_file, methods=["GET"])

    # GhostHub compatibility routes
    app.add_url_rule("/health", "compat_health", health_check, methods=["GET"])
    app.add_url_rule("/capabilities", "compat_capabilities", get_capabilities_endpoint, methods=["GET"])
    app.add_url_rule("/transcode", "compat_transcode", start_transcode, methods=["POST"])
    app.add_url_rule("/transcode/<job_id>", "compat_job_status", get_job_status, methods=["GET"])
    app.add_url_rule("/transcode/<job_id>", "compat_cancel_job", cancel_job, methods=["DELETE"])

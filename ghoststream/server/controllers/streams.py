"""Specter controller for GhostStream HLS and download endpoints."""

from __future__ import annotations

import logging
import time
from pathlib import Path

import gevent
from flask import Response, jsonify, request, send_file

from ...app.registry_keys import APP_CONFIG, JOB_MANAGER
from ...contracts.api import JobStatus
from ...specter.core.controller import Controller
from ...specter.core.registry import registry
from .security import append_token_to_playlist, require_stream_capability
from .websocket import track_http_client_header

logger = logging.getLogger(__name__)

PLAYLIST_STALE_THRESHOLD = 30.0


class StreamsController(Controller):
    def __init__(self) -> None:
        super().__init__("ghoststream_streams")

    @property
    def config(self):
        return registry.require(APP_CONFIG)

    @property
    def job_manager(self):
        return registry.require(JOB_MANAGER)

    def build_routes(self, router) -> None:
        @router.route("/stream/<job_id>/<path:filename>", methods=["GET"], endpoint="stream_file")
        def stream_file(job_id: str, filename: str):
            client_name = track_http_client_header()
            self.job_manager.note_job_client(job_id, client_name)
            stream_token = require_stream_capability(job_id)

            self.job_manager.touch_job(job_id)

            temp_dir = Path(self.config.transcoding.temp_directory)

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

            if filename.endswith(".m3u8") and not file_path.exists():
                job = self.job_manager.get_job(job_id, touch=False)
                if job and job.status in (JobStatus.PROCESSING, JobStatus.QUEUED):
                    for i in range(60):
                        gevent.sleep(0.5)
                        if file_path.exists():
                            break
                        if i % 10 == 9:
                            job = self.job_manager.get_job(job_id, touch=False)
                            if not job or job.status == JobStatus.ERROR:
                                break

            if not file_path.exists():
                return jsonify({"detail": "Stream file not found"}), 404

            if filename.endswith(".m3u8"):
                media_type = "application/vnd.apple.mpegurl"
                job = self.job_manager.get_job(job_id, touch=False)
                job_status = job.status if job else JobStatus.READY

                is_fresh, staleness = self._check_playlist_freshness(file_path, job_status)
                if not is_fresh and job and job_status == JobStatus.PROCESSING:
                    logger.warning(
                        "[Stream] Playlist stale for %.0fs, attempting restart for job %s",
                        staleness,
                        job_id,
                    )
                    new_job = self.job_manager.restart_stale_stream(job_id)
                    if new_job:
                        new_url = self.job_manager.build_stream_url(new_job) or f"/stream/{new_job.id}/{filename}"
                        logger.info("[Stream] Redirecting to restarted stream: %s", new_url)
                        return Response("", status=307, headers={"Location": new_url})

                    content = file_path.read_text()
                    content = self._inject_endlist_if_needed(content, job_status)
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
                content = self._inject_endlist_if_needed(content, job_status)
                content = append_token_to_playlist(content, stream_token)
                return Response(
                    content,
                    content_type=media_type,
                    headers={"Accept-Ranges": "bytes", "Cache-Control": "no-cache"},
                )

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
                    with open(file_path, "rb") as handle:
                        handle.seek(start)
                        remaining = content_length
                        while remaining > 0:
                            chunk_size = min(8192, remaining)
                            data = handle.read(chunk_size)
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

        @router.route("/download/<job_id>", methods=["GET"], endpoint="download_file")
        def download_file(job_id: str):
            client_name = track_http_client_header()
            self.job_manager.note_job_client(job_id, client_name)
            require_stream_capability(job_id)

            job = self.job_manager.get_job(job_id)
            if not job:
                return jsonify({"detail": "Job not found"}), 404

            if job.status != JobStatus.READY:
                return jsonify({"detail": "Job is not ready for download"}), 400

            if not job.output_path:
                return jsonify({"detail": "Output file not found"}), 404

            output_path = Path(job.output_path).resolve()

            if not output_path.exists():
                return jsonify({"detail": "Output file not found"}), 404

            return send_file(
                str(output_path),
                as_attachment=True,
                download_name=output_path.name,
            )

    def _inject_endlist_if_needed(self, content: str, job_status: JobStatus) -> str:
        if job_status == JobStatus.READY and "#EXT-X-ENDLIST" not in content:
            return content.rstrip() + "\n#EXT-X-ENDLIST\n"
        return content

    def _check_playlist_freshness(self, file_path: Path, job_status: JobStatus) -> tuple[bool, float]:
        if job_status != JobStatus.PROCESSING:
            return True, 0.0
        try:
            mtime = file_path.stat().st_mtime
            staleness = time.time() - mtime
            return staleness < PLAYLIST_STALE_THRESHOLD, staleness
        except Exception:
            return True, 0.0

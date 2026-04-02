"""Long-lived job orchestration service for GhostStream."""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import gevent
import gevent.lock
import gevent.queue

from ...contracts.api import JobStatus, TranscodeMode, TranscodeRequest
from ..domain.transcoding.models import TranscodeProgress
from ..infra.ffmpeg.engine import TranscodeEngine
from ..domain.jobs.models import Job
from ..domain.jobs.stats import JobStats
from ..domain.security.capabilities import CapabilityService

logger = logging.getLogger(__name__)


class JobManager:
    """Manages job queueing, execution, stream sharing, and cleanup."""

    def __init__(
        self,
        *,
        config,
        engine: TranscodeEngine,
        base_url: str = "http://localhost:8765",
        capability_service: Optional[CapabilityService] = None,
    ):
        self.config = config
        self.jobs: Dict[str, Job] = {}
        self.queue: gevent.queue.Queue = gevent.queue.Queue()
        self.active_jobs: Dict[str, gevent.Greenlet] = {}
        self.engine = engine
        self.stats = JobStats()
        self.base_url = base_url
        self.capability_service = capability_service
        self.progress_callbacks: List[Callable[[str, TranscodeProgress], None]] = []
        self.status_callbacks: List[Callable[[str, JobStatus], None]] = []
        self._workers: List[gevent.Greenlet] = []
        self._cleanup_greenlet: Optional[gevent.Greenlet] = None
        self._running = False

        self._create_lock = gevent.lock.BoundedSemaphore(1)

        self._cleanup_interval = 300
        self._job_ttl_streaming = 3600
        self._job_ttl_completed = self.config.transcoding.cleanup_after_hours * 3600

        self._shared_streams: Dict[str, str] = {}
        self._viewer_sessions: Dict[str, str] = {}

    def issue_control_token(self, job_id: str) -> Optional[str]:
        if not self.capability_service:
            return None
        return self.capability_service.mint(
            job_id=job_id,
            scope=CapabilityService.CONTROL_SCOPE,
        )

    def issue_stream_token(self, job_id: str) -> Optional[str]:
        if not self.capability_service:
            return None
        return self.capability_service.mint(
            job_id=job_id,
            scope=CapabilityService.STREAM_SCOPE,
        )

    def build_stream_url(self, job: Job) -> Optional[str]:
        if not job.stream_url:
            return None
        if not self.capability_service:
            return job.stream_url
        return self.capability_service.build_stream_url(
            job.stream_url,
            self.issue_stream_token(job.id),
        )

    def build_download_url(self, job: Job) -> Optional[str]:
        if not job.download_url:
            return None
        if not self.capability_service:
            return job.download_url
        return self.capability_service.build_stream_url(
            job.download_url,
            self.issue_stream_token(job.id),
        )

    def build_transcode_response(self, job: Job):
        return job.to_response(
            stream_url_override=self.build_stream_url(job),
            download_url_override=self.build_download_url(job),
            control_token=self.issue_control_token(job.id),
        )

    def build_status_response(self, job: Job):
        return job.to_status_response(
            stream_url_override=self.build_stream_url(job),
            download_url_override=self.build_download_url(job),
            control_token=self.issue_control_token(job.id),
        )

    def note_job_client(self, job_id: str, client_name: Optional[str]) -> None:
        if not client_name:
            return
        job = self.jobs.get(job_id)
        if job:
            job.client_names.add(client_name)

    def get_active_client_names(self) -> list[str]:
        names = set()
        for job in self.jobs.values():
            if job.cleaned_up:
                continue
            if job.status in (JobStatus.CANCELLED, JobStatus.ERROR):
                continue
            names.update(job.client_names)
        return sorted(names)

    def start(self) -> None:
        if self._running:
            return

        self._running = True
        max_workers = self.config.transcoding.max_concurrent_jobs

        self._cleanup_orphaned_dirs()

        for worker_id in range(max_workers):
            worker = gevent.spawn(self._worker, worker_id)
            self._workers.append(worker)

        self._cleanup_greenlet = gevent.spawn(self._cleanup_loop)

        logger.info("Started %s job workers + cleanup task", max_workers)

    def stop(self) -> None:
        self._running = False

        if self._cleanup_greenlet:
            self._cleanup_greenlet.kill(block=False)
            self._cleanup_greenlet = None

        for job_id, glet in list(self.active_jobs.items()):
            if job_id in self.jobs:
                self.jobs[job_id].cancel_event.set()
            if glet:
                glet.kill(block=False)
        self.active_jobs.clear()

        for worker in self._workers:
            worker.kill(block=False)
        self._workers.clear()
        self.progress_callbacks.clear()
        self.status_callbacks.clear()

        self._cleanup_all_jobs()

        logger.info("Job manager stopped")

    def _worker(self, worker_id: int) -> None:
        logger.info("Worker %s started", worker_id)

        while self._running:
            try:
                job_id = self.queue.get(timeout=1.0)
            except gevent.queue.Empty:
                continue
            except gevent.GreenletExit:
                break

            if job_id not in self.jobs:
                continue

            job = self.jobs[job_id]
            self.active_jobs[job_id] = None

            try:
                self._process_job(job)
            except Exception as exc:
                logger.exception("Worker %s error processing job %s: %s", worker_id, job_id, exc)
                job.status = JobStatus.ERROR
                job.error_message = str(exc)
                job.completed_at = datetime.utcnow()
                self._notify_status(job_id, JobStatus.ERROR)
            finally:
                if job_id in self.active_jobs:
                    del self.active_jobs[job_id]
                self.stats.record_job_complete(job, job.status == JobStatus.READY)

        logger.info("Worker %s stopped", worker_id)

    def _process_job(self, job: Job) -> None:
        job.status = JobStatus.PROCESSING
        job.started_at = datetime.utcnow()

        if job.request.mode in [TranscodeMode.STREAM, TranscodeMode.ABR]:
            job.stream_url = f"{self.base_url}/stream/{job.id}/master.m3u8"

        self._notify_status(job.id, JobStatus.PROCESSING)

        media_info = self.engine.get_media_info(job.request.source)
        job.duration = media_info.duration

        def progress_callback(progress: TranscodeProgress):
            job.progress = progress.percent
            job.current_time = progress.time

            if progress.speed > 0 and job.duration > 0:
                remaining_time = job.duration - progress.time
                job.eta_seconds = int(remaining_time / progress.speed)

            self._notify_progress(job.id, progress)

        if job.request.mode == TranscodeMode.ABR:
            success, result, hw_accel = self.engine.transcode_abr(
                job_id=job.id,
                source=job.request.source,
                output_config=job.request.output,
                start_time=job.request.start_time,
                progress_callback=progress_callback,
                cancel_event=job.cancel_event,
                subtitles=job.request.subtitles,
            )
        else:
            success, result, hw_accel = self.engine.transcode(
                job_id=job.id,
                source=job.request.source,
                mode=job.request.mode,
                output_config=job.request.output,
                start_time=job.request.start_time,
                progress_callback=progress_callback,
                cancel_event=job.cancel_event,
                subtitles=job.request.subtitles,
            )

        job.hw_accel_used = hw_accel
        job.completed_at = datetime.utcnow()

        if job.cancel_event.is_set():
            job.status = JobStatus.CANCELLED
            self._notify_status(job.id, JobStatus.CANCELLED)
            self.engine.cleanup_job(job.id)
            return

        if success:
            job.status = JobStatus.READY
            job.progress = 100.0
            job.output_path = result

            if job.request.mode in (TranscodeMode.STREAM, TranscodeMode.ABR):
                job.stream_url = f"{self.base_url}/stream/{job.id}/master.m3u8"
            else:
                job.download_url = f"{self.base_url}/download/{job.id}"

            self._notify_status(job.id, JobStatus.READY)

            if job.request.callback_url:
                self._send_callback(job)
        else:
            job.status = JobStatus.ERROR
            job.error_message = result
            self._notify_status(job.id, JobStatus.ERROR)

    def _send_callback(self, job: Job) -> None:
        if not job.request.callback_url:
            return

        import httpx

        try:
            with httpx.Client(timeout=10.0) as client:
                client.post(
                    job.request.callback_url,
                    json=self.build_transcode_response(job).model_dump(),
                )
            logger.info("Callback sent to %s", job.request.callback_url)
        except Exception as exc:
            logger.error("Failed to send callback: %s", exc)

    def _notify_progress(self, job_id: str, progress: TranscodeProgress) -> None:
        for callback in self.progress_callbacks:
            try:
                callback(job_id, progress)
            except Exception as exc:
                logger.error("Progress callback error: %s", exc)

    def _notify_status(self, job_id: str, status: JobStatus) -> None:
        for callback in self.status_callbacks:
            try:
                callback(job_id, status)
            except Exception as exc:
                logger.error("Status callback error: %s", exc)

    def register_progress_callback(self, callback: Callable[[str, TranscodeProgress], None]) -> None:
        if callback not in self.progress_callbacks:
            self.progress_callbacks.append(callback)

    def register_status_callback(self, callback: Callable[[str, JobStatus], None]) -> None:
        if callback not in self.status_callbacks:
            self.status_callbacks.append(callback)

    def _generate_stream_key(self, request: TranscodeRequest) -> str:
        key_parts = [
            request.source.rstrip("/"),
            request.mode.value,
            request.output.format.value if request.output else "hls",
            request.output.resolution.value if request.output else "original",
        ]
        key_string = "|".join(key_parts)
        stream_key = hashlib.sha256(key_string.encode()).hexdigest()[:16]
        logger.debug("[StreamShare] Generated key %s for source: %s...", stream_key, request.source[:50])
        return stream_key

    def _is_stream_shareable(self, job: Job) -> bool:
        if job.request.mode not in [TranscodeMode.STREAM, TranscodeMode.ABR]:
            return False
        if job.status not in [JobStatus.QUEUED, JobStatus.PROCESSING, JobStatus.READY]:
            return False
        if job.cleaned_up or job.cancel_event.is_set():
            return False
        if job.status == JobStatus.READY and job.progress < 99.0:
            logger.warning(
                "[StreamShare] Job %s marked READY but progress only %s%% - not shareable",
                job.id,
                job.progress,
            )
            return False
        return True

    def create_job(
        self,
        request: TranscodeRequest,
        session_id: Optional[str] = None,
        client_name: Optional[str] = None,
    ) -> Job:
        with self._create_lock:
            if request.mode in [TranscodeMode.STREAM, TranscodeMode.ABR]:
                stream_key = self._generate_stream_key(request)

                if stream_key in self._shared_streams:
                    existing_job_id = self._shared_streams[stream_key]
                    existing_job = self.jobs.get(existing_job_id)

                    if existing_job and self._is_stream_shareable(existing_job):
                        is_new_viewer = True
                        if session_id:
                            if self._viewer_sessions.get(session_id) == existing_job_id:
                                is_new_viewer = False
                            else:
                                self._viewer_sessions[session_id] = existing_job_id

                        if is_new_viewer:
                            existing_job.viewer_count += 1
                            existing_job.is_shared = existing_job.viewer_count > 1
                            if client_name:
                                existing_job.client_names.add(client_name)
                            logger.info(
                                "[StreamShare] Viewer joined existing stream %s (viewers: %s, source: %s...)",
                                existing_job_id,
                                existing_job.viewer_count,
                                request.source[:50],
                            )

                        existing_job.last_accessed = datetime.utcnow()
                        return existing_job

                    if existing_job:
                        logger.info(
                            "[StreamShare] Existing job %s not shareable: status=%s, cleaned_up=%s, cancelled=%s, progress=%s",
                            existing_job_id,
                            existing_job.status.value,
                            existing_job.cleaned_up,
                            existing_job.cancel_event.is_set(),
                            existing_job.progress,
                        )
                    else:
                        logger.info("[StreamShare] Job %s no longer exists in registry", existing_job_id)
                    if stream_key in self._shared_streams:
                        del self._shared_streams[stream_key]
                else:
                    logger.debug("[StreamShare] No existing stream for key %s, creating new", stream_key)

                job_id = str(uuid.uuid4())
                job = Job(id=job_id, request=request, stream_key=stream_key, viewer_count=1)
                job.stream_url = f"{self.base_url}/stream/{job_id}/master.m3u8"
                if client_name:
                    job.client_names.add(client_name)

                self.jobs[job_id] = job
                self._shared_streams[stream_key] = job_id

                if session_id:
                    self._viewer_sessions[session_id] = job_id

                self.queue.put(job_id)

                logger.info("[StreamShare] Created new shared stream %s for source: %s...", job_id, request.source[:50])
                return job

            job_id = str(uuid.uuid4())
            job = Job(id=job_id, request=request)
            if client_name:
                job.client_names.add(client_name)

            if request.mode == TranscodeMode.STREAM:
                job.stream_url = f"{self.base_url}/stream/{job_id}/master.m3u8"

            self.jobs[job_id] = job
            self.queue.put(job_id)

            logger.info("Created job %s for source: %s", job_id, request.source)
            return job

    def leave_stream(self, job_id: str, session_id: Optional[str] = None) -> bool:
        with self._create_lock:
            job = self.jobs.get(job_id)
            if not job:
                return False

            if session_id and session_id in self._viewer_sessions:
                del self._viewer_sessions[session_id]

            if job.viewer_count > 0:
                job.viewer_count -= 1
                logger.info("[StreamShare] Viewer left stream %s (viewers remaining: %s)", job_id, job.viewer_count)

            if job.viewer_count == 0 and job.stream_key:
                job.is_shared = False

            return job.viewer_count > 0

    def get_shared_stream_stats(self) -> Dict[str, Any]:
        active_shared = 0
        total_viewers = 0
        streams_by_source = {}

        for job_id in self._shared_streams.values():
            job = self.jobs.get(job_id)
            if job and self._is_stream_shareable(job):
                active_shared += 1
                total_viewers += job.viewer_count
                source_short = job.request.source[:50]
                streams_by_source[source_short] = {
                    "job_id": job_id,
                    "viewers": job.viewer_count,
                    "status": job.status.value,
                    "progress": job.progress,
                }

        return {
            "active_shared_streams": active_shared,
            "total_viewers": total_viewers,
            "viewer_sessions": len(self._viewer_sessions),
            "streams": streams_by_source,
        }

    def get_job(self, job_id: str, touch: bool = True) -> Optional[Job]:
        job = self.jobs.get(job_id)
        if job and touch:
            job.last_accessed = datetime.utcnow()
        return job

    def cancel_job(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if not job:
            return False

        if job.status in [JobStatus.READY, JobStatus.ERROR, JobStatus.CANCELLED]:
            return False

        job.cancel_event.set()
        job.status = JobStatus.CANCELLED
        job.completed_at = datetime.utcnow()
        if self.capability_service:
            self.capability_service.revoke_job(job_id)

        if job.stream_key and job.stream_key in self._shared_streams:
            del self._shared_streams[job.stream_key]
            logger.info("[StreamShare] Removed cancelled stream %s from shared streams", job_id)

        self.engine.cleanup_job(job_id)
        job.cleaned_up = True

        logger.info("Cancelled job %s", job_id)
        return True

    def get_queue_length(self) -> int:
        return self.queue.qsize()

    def get_active_count(self) -> int:
        return len(self.active_jobs)

    def touch_job(self, job_id: str) -> None:
        job = self.jobs.get(job_id)
        if job:
            job.last_accessed = datetime.utcnow()

    def restart_stale_stream(self, job_id: str) -> Optional[Job]:
        job = self.jobs.get(job_id)
        if not job:
            return None

        if job.request.mode not in [TranscodeMode.STREAM, TranscodeMode.ABR]:
            return None

        logger.info("[StreamRestart] Restarting stale stream %s", job_id)

        old_stream_key = job.stream_key
        job.cancel_event.set()

        gevent.sleep(0.5)

        self.engine.cleanup_job(job_id)
        job.cleaned_up = True
        job.status = JobStatus.CANCELLED

        if old_stream_key and old_stream_key in self._shared_streams:
            del self._shared_streams[old_stream_key]

        if job_id in self.jobs:
            del self.jobs[job_id]

        new_job = self.create_job(job.request)
        new_job.client_names.update(job.client_names)

        logger.info("[StreamRestart] Created new job %s to replace stale %s", new_job.id, job_id)
        return new_job

    def cleanup_job(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if not job:
            return False

        if not job.cleaned_up:
            self.engine.cleanup_job(job_id)
            job.cleaned_up = True
            if self.capability_service:
                self.capability_service.revoke_job(job_id)
            logger.info("[Cleanup] Cleaned up job %s", job_id)

        return True

    def remove_job(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if not job:
            return False

        if not job.cleaned_up:
            self.cleanup_job(job_id)

        if self.capability_service:
            self.capability_service.revoke_job(job_id)

        if job.stream_key and job.stream_key in self._shared_streams:
            del self._shared_streams[job.stream_key]

        sessions_to_remove = [sid for sid, jid in self._viewer_sessions.items() if jid == job_id]
        for sid in sessions_to_remove:
            del self._viewer_sessions[sid]

        del self.jobs[job_id]
        logger.debug("[Cleanup] Removed job %s from tracking", job_id)
        return True

    def _cleanup_loop(self) -> None:
        logger.info("[Cleanup] Starting cleanup loop")

        orphan_cleanup_counter = 0
        worker_health_counter = 0

        while self._running:
            try:
                gevent.sleep(self._cleanup_interval)
                self._cleanup_stale_jobs()

                orphan_cleanup_counter += 1
                if orphan_cleanup_counter >= 3:
                    self._cleanup_orphaned_dirs()
                    orphan_cleanup_counter = 0

                worker_health_counter += 1
                if worker_health_counter >= 1:
                    self._check_worker_health()
                    worker_health_counter = 0

            except gevent.GreenletExit:
                break
            except Exception as exc:
                logger.error("[Cleanup] Error in cleanup loop: %s", exc)

        logger.info("[Cleanup] Cleanup loop stopped")

    def _cleanup_stale_jobs(self) -> int:
        now = datetime.utcnow()
        cleaned = 0

        jobs_to_remove = []

        for job_id, job in list(self.jobs.items()):
            if job.status == JobStatus.PROCESSING and job.request.mode in (TranscodeMode.STREAM, TranscodeMode.ABR):
                time_since_access = (now - job.last_accessed).total_seconds()

                time_since_start = 0
                if job.started_at:
                    time_since_start = (now - job.started_at).total_seconds()

                if time_since_start > 120 and time_since_access > 300:
                    logger.info("[Cleanup] Job %s stalled (no access for %.0fs), cancelling", job_id, time_since_access)
                    self.cancel_job(job_id)
                    continue

            if job.status in (JobStatus.QUEUED, JobStatus.PROCESSING):
                continue

            if job.viewer_count > 0:
                continue

            if job.completed_at:
                age = (now - job.last_accessed).total_seconds()
                ttl = self._job_ttl_streaming if job.request.mode in (TranscodeMode.STREAM, TranscodeMode.ABR) else self._job_ttl_completed
                if age > ttl:
                    jobs_to_remove.append(job_id)

        for job_id in jobs_to_remove:
            self.cleanup_job(job_id)
            cleaned += 1

        metadata_ttl = 86400
        for job_id, job in list(self.jobs.items()):
            if job.cleaned_up and job.completed_at:
                age = (now - job.completed_at).total_seconds()
                if age > metadata_ttl:
                    del self.jobs[job_id]

        if cleaned > 0:
            logger.info("[Cleanup] Cleaned up %s stale job(s)", cleaned)

        return cleaned

    def _cleanup_orphaned_dirs(self) -> int:
        temp_dir = Path(self.engine.temp_dir)
        if not temp_dir.exists():
            return 0

        cleaned = 0
        known_job_ids = set(self.jobs.keys())

        for item in temp_dir.iterdir():
            if item.is_dir():
                job_id = item.name
                if job_id not in known_job_ids:
                    try:
                        import shutil

                        shutil.rmtree(item, ignore_errors=True)
                        cleaned += 1
                        logger.info("[Cleanup] Removed orphaned temp dir: %s", job_id)
                    except Exception as exc:
                        logger.warning("[Cleanup] Failed to remove orphaned dir %s: %s", job_id, exc)

        if cleaned > 0:
            logger.info("[Cleanup] Cleaned up %s orphaned temp dir(s)", cleaned)

        return cleaned

    def _check_worker_health(self) -> None:
        dead_workers = []

        for worker_id, worker in enumerate(self._workers):
            if worker.dead:
                exc = worker.exception
                if exc:
                    logger.error("[WorkerHealth] Worker %s crashed with: %s", worker_id, exc)
                dead_workers.append(worker_id)

        if dead_workers and self._running:
            logger.warning("[WorkerHealth] %s worker(s) died, restarting...", len(dead_workers))

            self._workers = [worker for index, worker in enumerate(self._workers) if index not in dead_workers]

            for _ in range(len(dead_workers)):
                new_worker_id = len(self._workers)
                new_worker = gevent.spawn(self._worker, new_worker_id)
                self._workers.append(new_worker)
                logger.info("[WorkerHealth] Started replacement worker %s", new_worker_id)

    def _cleanup_all_jobs(self) -> None:
        logger.info("[Cleanup] Cleaning up %s job(s) on shutdown", len(self.jobs))

        for job_id in list(self.jobs.keys()):
            try:
                self.cleanup_job(job_id)
            except Exception as exc:
                logger.warning("[Cleanup] Error cleaning job %s: %s", job_id, exc)

    def run_cleanup(self) -> Dict[str, int]:
        return {
            "stale_jobs_cleaned": self._cleanup_stale_jobs(),
            "orphaned_dirs_cleaned": self._cleanup_orphaned_dirs(),
        }

    def get_cleanup_stats(self) -> Dict[str, Any]:
        now = datetime.utcnow()

        active = 0
        ready = 0
        cleaned = 0
        stale = 0

        for job in self.jobs.values():
            if job.status in (JobStatus.QUEUED, JobStatus.PROCESSING):
                active += 1
            elif job.cleaned_up:
                cleaned += 1
            elif job.status == JobStatus.READY:
                ready += 1
                if job.completed_at:
                    age = (now - job.last_accessed).total_seconds()
                    ttl = self._job_ttl_streaming if job.request.mode in (TranscodeMode.STREAM, TranscodeMode.ABR) else self._job_ttl_completed
                    if age > ttl * 0.8:
                        stale += 1

        return {
            "total_jobs": len(self.jobs),
            "active_jobs": active,
            "ready_jobs": ready,
            "cleaned_jobs": cleaned,
            "nearly_stale": stale,
            "temp_dir": str(self.engine.temp_dir),
        }

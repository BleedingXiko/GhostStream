"""
Job queue and management for GhostStream — Specter-native (gevent).
"""

import uuid
import logging
import hashlib
import gevent
import gevent.queue
import gevent.lock
from datetime import datetime
from typing import Dict, Optional, List, Callable, Any
from pathlib import Path

from ..models import JobStatus, TranscodeRequest, TranscodeMode
from ..security import CapabilityService
from ..transcoding import TranscodeEngine, TranscodeProgress
from ..config import get_config
from .models import Job
from .stats import JobStats

logger = logging.getLogger(__name__)


class JobManager:
    """Manages the job queue and execution with proper lifecycle tracking."""
    
    def __init__(
        self,
        base_url: str = "http://localhost:8765",
        capability_service: Optional[CapabilityService] = None,
    ):
        self.config = get_config()
        self.jobs: Dict[str, Job] = {}
        self.queue: gevent.queue.Queue = gevent.queue.Queue()
        self.active_jobs: Dict[str, gevent.Greenlet] = {}
        self.engine = TranscodeEngine()
        self.stats = JobStats()
        self.base_url = base_url
        self.capability_service = capability_service
        self.progress_callbacks: List[Callable[[str, TranscodeProgress], None]] = []
        self.status_callbacks: List[Callable[[str, JobStatus], None]] = []
        self._workers: List[gevent.Greenlet] = []
        self._cleanup_greenlet: Optional[gevent.Greenlet] = None
        self._running = False
        
        # Concurrency control
        self._create_lock = gevent.lock.BoundedSemaphore(1)
        
        # Cleanup settings
        self._cleanup_interval = 300  # 5 minutes
        self._job_ttl_streaming = 3600  # 1 hour for streaming jobs
        self._job_ttl_completed = self.config.transcoding.cleanup_after_hours * 3600
        
        # Stream sharing: maps stream_key -> job_id for active HLS streams
        self._shared_streams: Dict[str, str] = {}
        # Track which viewer sessions are using which job
        self._viewer_sessions: Dict[str, str] = {}  # session_id -> job_id

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
        
    def start(self) -> None:
        """Start the job manager workers and cleanup task."""
        if self._running:
            return
        
        self._running = True
        max_workers = self.config.transcoding.max_concurrent_jobs
        
        # Clean up orphaned temp directories on startup
        self._cleanup_orphaned_dirs()
        
        for i in range(max_workers):
            worker = gevent.spawn(self._worker, i)
            self._workers.append(worker)
        
        # Start background cleanup task
        self._cleanup_greenlet = gevent.spawn(self._cleanup_loop)
        
        logger.info(f"Started {max_workers} job workers + cleanup task")
    
    def stop(self) -> None:
        """Stop the job manager and cancel all workers."""
        self._running = False
        
        # Cancel cleanup task
        if self._cleanup_greenlet:
            self._cleanup_greenlet.kill(block=False)
            self._cleanup_greenlet = None
        
        # Cancel all active jobs
        for job_id, glet in self.active_jobs.items():
            if job_id in self.jobs:
                self.jobs[job_id].cancel_event.set()
            if glet:
                glet.kill(block=False)
        
        # Cancel workers
        for worker in self._workers:
            worker.kill(block=False)
        
        gevent.joinall(self._workers, timeout=10)
        self._workers.clear()
        
        # Final cleanup of all jobs
        self._cleanup_all_jobs()
        
        logger.info("Job manager stopped")
    
    def _worker(self, worker_id: int) -> None:
        """Worker greenlet that processes jobs from the queue."""
        logger.info(f"Worker {worker_id} started")
        
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
            self.active_jobs[job_id] = None  # Track as active
            
            try:
                self._process_job(job)
            except Exception as e:
                logger.exception(f"Worker {worker_id} error processing job {job_id}: {e}")
                job.status = JobStatus.ERROR
                job.error_message = str(e)
                job.completed_at = datetime.utcnow()
                self._notify_status(job_id, JobStatus.ERROR)
            finally:
                if job_id in self.active_jobs:
                    del self.active_jobs[job_id]
                self.stats.record_job_complete(job, job.status == JobStatus.READY)
        
        logger.info(f"Worker {worker_id} stopped")
    
    def _process_job(self, job: Job) -> None:
        """Process a single job."""
        job.status = JobStatus.PROCESSING
        job.started_at = datetime.utcnow()
        
        # For streaming modes, set stream_url early so clients can start polling
        if job.request.mode in [TranscodeMode.STREAM, TranscodeMode.ABR]:
            job.stream_url = f"{self.base_url}/stream/{job.id}/master.m3u8"
        
        self._notify_status(job.id, JobStatus.PROCESSING)
        
        # Get media info for duration
        media_info = self.engine.get_media_info(job.request.source)
        job.duration = media_info.duration
        
        def progress_callback(progress: TranscodeProgress):
            job.progress = progress.percent
            job.current_time = progress.time
            
            # Calculate ETA
            if progress.speed > 0 and job.duration > 0:
                remaining_time = job.duration - progress.time
                job.eta_seconds = int(remaining_time / progress.speed)
            
            self._notify_progress(job.id, progress)
        
        # Choose transcoding method based on mode
        if job.request.mode == TranscodeMode.ABR:
            success, result, hw_accel = self.engine.transcode_abr(
                job_id=job.id,
                source=job.request.source,
                output_config=job.request.output,
                start_time=job.request.start_time,
                progress_callback=progress_callback,
                cancel_event=job.cancel_event,
                subtitles=job.request.subtitles
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
                subtitles=job.request.subtitles
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
            
            if job.request.mode == TranscodeMode.STREAM:
                job.stream_url = f"{self.base_url}/stream/{job.id}/master.m3u8"
            elif job.request.mode == TranscodeMode.ABR:
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
        """Send callback to the configured URL."""
        if not job.request.callback_url:
            return
        
        import httpx
        
        try:
            with httpx.Client(timeout=10.0) as client:
                client.post(
                    job.request.callback_url,
                    json=self.build_transcode_response(job).model_dump(),
                )
            logger.info(f"Callback sent to {job.request.callback_url}")
        except Exception as e:
            logger.error(f"Failed to send callback: {e}")
    
    def _notify_progress(self, job_id: str, progress: TranscodeProgress) -> None:
        """Notify all registered progress callbacks."""
        for callback in self.progress_callbacks:
            try:
                callback(job_id, progress)
            except Exception as e:
                logger.error(f"Progress callback error: {e}")
    
    def _notify_status(self, job_id: str, status: JobStatus) -> None:
        """Notify all registered status callbacks."""
        for callback in self.status_callbacks:
            try:
                callback(job_id, status)
            except Exception as e:
                logger.error(f"Status callback error: {e}")
    
    def register_progress_callback(self, callback: Callable[[str, TranscodeProgress], None]) -> None:
        """Register a progress callback."""
        self.progress_callbacks.append(callback)
    
    def register_status_callback(self, callback: Callable[[str, JobStatus], None]) -> None:
        """Register a status callback."""
        self.status_callbacks.append(callback)
    
    def _generate_stream_key(self, request: TranscodeRequest) -> str:
        """Generate a unique key for stream sharing based on source and output config."""
        key_parts = [
            request.source.rstrip('/'),
            request.mode.value,
            request.output.format.value if request.output else "hls",
            request.output.resolution.value if request.output else "original",
        ]
        key_string = "|".join(key_parts)
        stream_key = hashlib.sha256(key_string.encode()).hexdigest()[:16]
        logger.debug(f"[StreamShare] Generated key {stream_key} for source: {request.source[:50]}...")
        return stream_key
    
    def _is_stream_shareable(self, job: Job) -> bool:
        """Check if a job's stream can be shared with new viewers."""
        if job.request.mode not in [TranscodeMode.STREAM, TranscodeMode.ABR]:
            return False
        if job.status not in [JobStatus.QUEUED, JobStatus.PROCESSING, JobStatus.READY]:
            return False
        if job.cleaned_up or job.cancel_event.is_set():
            return False
        if job.status == JobStatus.READY and job.progress < 99.0:
            logger.warning(f"[StreamShare] Job {job.id} marked READY but progress only {job.progress}% - not shareable")
            return False
        return True
    
    def create_job(self, request: TranscodeRequest, session_id: Optional[str] = None) -> Job:
        """
        Create a new transcoding job or return existing shared stream.
        
        Thread-safe via gevent BoundedSemaphore.
        """
        with self._create_lock:
            # Check for stream sharing on HLS/ABR streaming requests
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
                            logger.info(
                                f"[StreamShare] Viewer joined existing stream {existing_job_id} "
                                f"(viewers: {existing_job.viewer_count}, source: {request.source[:50]}...)"
                            )
                        
                        existing_job.last_accessed = datetime.utcnow()
                        return existing_job
                    else:
                        if existing_job:
                            logger.info(
                                f"[StreamShare] Existing job {existing_job_id} not shareable: "
                                f"status={existing_job.status.value}, cleaned_up={existing_job.cleaned_up}, "
                                f"cancelled={existing_job.cancel_event.is_set()}, progress={existing_job.progress}"
                            )
                        else:
                            logger.info(f"[StreamShare] Job {existing_job_id} no longer exists in registry")
                        if stream_key in self._shared_streams:
                            del self._shared_streams[stream_key]
                else:
                    logger.debug(f"[StreamShare] No existing stream for key {stream_key}, creating new")
                
                job_id = str(uuid.uuid4())
                job = Job(id=job_id, request=request, stream_key=stream_key, viewer_count=1)
                job.stream_url = f"{self.base_url}/stream/{job_id}/master.m3u8"
                
                self.jobs[job_id] = job
                self._shared_streams[stream_key] = job_id
                
                if session_id:
                    self._viewer_sessions[session_id] = job_id
                
                self.queue.put(job_id)
                
                logger.info(f"[StreamShare] Created new shared stream {job_id} for source: {request.source[:50]}...")
                return job
            
            # Non-streaming jobs: create normally without sharing
            job_id = str(uuid.uuid4())
            job = Job(id=job_id, request=request)
            
            if request.mode == TranscodeMode.STREAM:
                job.stream_url = f"{self.base_url}/stream/{job_id}/master.m3u8"
            
            self.jobs[job_id] = job
            self.queue.put(job_id)
            
            logger.info(f"Created job {job_id} for source: {request.source}")
            return job
    
    def leave_stream(self, job_id: str, session_id: Optional[str] = None) -> bool:
        """Decrement viewer count when a viewer leaves a shared stream."""
        with self._create_lock:
            job = self.jobs.get(job_id)
            if not job:
                return False
            
            if session_id and session_id in self._viewer_sessions:
                del self._viewer_sessions[session_id]
            
            if job.viewer_count > 0:
                job.viewer_count -= 1
                logger.info(f"[StreamShare] Viewer left stream {job_id} (viewers remaining: {job.viewer_count})")
            
            if job.viewer_count == 0 and job.stream_key:
                job.is_shared = False
            
            return job.viewer_count > 0
    
    def get_shared_stream_stats(self) -> Dict[str, Any]:
        """Get statistics about shared streams."""
        active_shared = 0
        total_viewers = 0
        streams_by_source = {}
        
        for stream_key, job_id in self._shared_streams.items():
            job = self.jobs.get(job_id)
            if job and self._is_stream_shareable(job):
                active_shared += 1
                total_viewers += job.viewer_count
                source_short = job.request.source[:50]
                streams_by_source[source_short] = {
                    "job_id": job_id,
                    "viewers": job.viewer_count,
                    "status": job.status.value,
                    "progress": job.progress
                }
        
        return {
            "active_shared_streams": active_shared,
            "total_viewers": total_viewers,
            "viewer_sessions": len(self._viewer_sessions),
            "streams": streams_by_source
        }
    
    def get_job(self, job_id: str, touch: bool = True) -> Optional[Job]:
        """Get a job by ID. Updates last_accessed if touch=True."""
        job = self.jobs.get(job_id)
        if job and touch:
            job.last_accessed = datetime.utcnow()
        return job
    
    def cancel_job(self, job_id: str) -> bool:
        """Cancel a job."""
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
            logger.info(f"[StreamShare] Removed cancelled stream {job_id} from shared streams")
        
        self.engine.cleanup_job(job_id)
        job.cleaned_up = True
        
        logger.info(f"Cancelled job {job_id}")
        return True
    
    def get_queue_length(self) -> int:
        """Get the current queue length."""
        return self.queue.qsize()
    
    def get_active_count(self) -> int:
        """Get the number of active jobs."""
        return len(self.active_jobs)
    
    def get_all_jobs(self) -> List[Job]:
        """Get all jobs."""
        return list(self.jobs.values())
    
    def touch_job(self, job_id: str) -> None:
        """Update last_accessed time for a job."""
        job = self.jobs.get(job_id)
        if job:
            job.last_accessed = datetime.utcnow()
    
    def restart_stale_stream(self, job_id: str) -> Optional[Job]:
        """Restart a stale streaming job."""
        job = self.jobs.get(job_id)
        if not job:
            return None
        
        if job.request.mode not in [TranscodeMode.STREAM, TranscodeMode.ABR]:
            return None
        
        logger.info(f"[StreamRestart] Restarting stale stream {job_id}")
        
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
        
        logger.info(f"[StreamRestart] Created new job {new_job.id} to replace stale {job_id}")
        return new_job
    
    def is_stream_stale(self, job_id: str, stale_threshold: float = 30.0) -> bool:
        """Check if a streaming job's output is stale (not being updated)."""
        job = self.jobs.get(job_id)
        if not job:
            return False
        
        if job.request.mode not in [TranscodeMode.STREAM, TranscodeMode.ABR]:
            return False
        
        if job.status != JobStatus.PROCESSING:
            return False
        
        import time
        
        config = get_config()
        playlist_path = Path(config.transcoding.temp_directory) / job_id / "master.m3u8"
        
        if not playlist_path.exists():
            if job.started_at:
                time_since_start = (datetime.utcnow() - job.started_at).total_seconds()
                if time_since_start > 60:
                    return True
            return False
        
        try:
            mtime = playlist_path.stat().st_mtime
            staleness = time.time() - mtime
            return staleness > stale_threshold
        except Exception:
            return False
    
    def cleanup_job(self, job_id: str) -> bool:
        """Explicitly clean up a job's temp files and remove from tracking."""
        job = self.jobs.get(job_id)
        if not job:
            return False
        
        if not job.cleaned_up:
            self.engine.cleanup_job(job_id)
            job.cleaned_up = True
            if self.capability_service:
                self.capability_service.revoke_job(job_id)
            logger.info(f"[Cleanup] Cleaned up job {job_id}")
        
        return True
    
    def remove_job(self, job_id: str) -> bool:
        """Remove a job from tracking (after cleanup)."""
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
        logger.debug(f"[Cleanup] Removed job {job_id} from tracking")
        return True
    
    def _cleanup_loop(self) -> None:
        """Background greenlet that periodically cleans up stale jobs."""
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
            except Exception as e:
                logger.error(f"[Cleanup] Error in cleanup loop: {e}")
        
        logger.info("[Cleanup] Cleanup loop stopped")
    
    def _cleanup_stale_jobs(self) -> int:
        """Clean up jobs that haven't been accessed recently."""
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
                    logger.info(f"[Cleanup] Job {job_id} stalled (no access for {time_since_access:.0f}s), cancelling")
                    self.cancel_job(job_id)
                    continue

            if job.status in (JobStatus.QUEUED, JobStatus.PROCESSING):
                continue
            
            if job.viewer_count > 0:
                continue
            
            if job.completed_at:
                age = (now - job.last_accessed).total_seconds()
                
                if job.request.mode in (TranscodeMode.STREAM, TranscodeMode.ABR):
                    ttl = self._job_ttl_streaming
                else:
                    ttl = self._job_ttl_completed
                
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
            logger.info(f"[Cleanup] Cleaned up {cleaned} stale job(s)")
        
        return cleaned
    
    def _cleanup_orphaned_dirs(self) -> int:
        """Clean up temp directories that don't have a matching job."""
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
                        logger.info(f"[Cleanup] Removed orphaned temp dir: {job_id}")
                    except Exception as e:
                        logger.warning(f"[Cleanup] Failed to remove orphaned dir {job_id}: {e}")
        
        if cleaned > 0:
            logger.info(f"[Cleanup] Cleaned up {cleaned} orphaned temp dir(s)")
        
        return cleaned
    
    def _check_worker_health(self) -> None:
        """Check if all workers are still running and restart any that crashed."""
        max_workers = self.config.transcoding.max_concurrent_jobs
        
        alive_workers = []
        dead_workers = []
        
        for i, worker in enumerate(self._workers):
            if worker.dead:
                exc = worker.exception
                if exc:
                    logger.error(f"[WorkerHealth] Worker {i} crashed with: {exc}")
                dead_workers.append(i)
            else:
                alive_workers.append(i)
        
        if dead_workers and self._running:
            logger.warning(f"[WorkerHealth] {len(dead_workers)} worker(s) died, restarting...")
            
            self._workers = [w for i, w in enumerate(self._workers) if i not in dead_workers]
            
            for i in range(len(dead_workers)):
                new_worker_id = len(self._workers)
                new_worker = gevent.spawn(self._worker, new_worker_id)
                self._workers.append(new_worker)
                logger.info(f"[WorkerHealth] Started replacement worker {new_worker_id}")
    
    def _cleanup_all_jobs(self) -> None:
        """Clean up all jobs (called on shutdown)."""
        logger.info(f"[Cleanup] Cleaning up {len(self.jobs)} job(s) on shutdown")
        
        for job_id in list(self.jobs.keys()):
            try:
                self.cleanup_job(job_id)
            except Exception as e:
                logger.warning(f"[Cleanup] Error cleaning job {job_id}: {e}")
    
    def get_cleanup_stats(self) -> Dict[str, Any]:
        """Get statistics about job cleanup."""
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
            "temp_dir": str(self.engine.temp_dir)
        }


# Global job manager instance
_job_manager: Optional[JobManager] = None


def get_job_manager() -> JobManager:
    """Get the global job manager instance."""
    global _job_manager
    if _job_manager is None:
        _job_manager = JobManager()
    return _job_manager


def set_job_manager(manager: JobManager) -> None:
    """Set the global job manager instance."""
    global _job_manager
    _job_manager = manager

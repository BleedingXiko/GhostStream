"""
Job queue and management for GhostStream
"""

import asyncio
import uuid
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional, List, Callable, Any
from dataclasses import dataclass, field
from pathlib import Path

from .models import (
    TranscodeRequest, TranscodeResponse, JobStatus, JobStatusResponse,
    TranscodeMode
)
from .transcoder import TranscodeEngine, TranscodeProgress
from .config import get_config

logger = logging.getLogger(__name__)


@dataclass
class Job:
    """Represents a transcoding job."""
    id: str
    request: TranscodeRequest
    status: JobStatus = JobStatus.QUEUED
    progress: float = 0.0
    current_time: float = 0.0
    duration: float = 0.0
    stream_url: Optional[str] = None
    download_url: Optional[str] = None
    output_path: Optional[str] = None
    eta_seconds: Optional[int] = None
    hw_accel_used: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    last_accessed: datetime = field(default_factory=datetime.utcnow)
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    cleaned_up: bool = False
    
    def to_response(self) -> TranscodeResponse:
        return TranscodeResponse(
            job_id=self.id,
            status=self.status,
            progress=self.progress,
            stream_url=self.stream_url,
            download_url=self.download_url,
            eta_seconds=self.eta_seconds,
            hw_accel_used=self.hw_accel_used,
            error_message=self.error_message
        )
    
    def to_status_response(self) -> JobStatusResponse:
        return JobStatusResponse(
            job_id=self.id,
            status=self.status,
            progress=self.progress,
            current_time=self.current_time,
            duration=self.duration,
            stream_url=self.stream_url,
            download_url=self.download_url,
            eta_seconds=self.eta_seconds,
            hw_accel_used=self.hw_accel_used,
            error_message=self.error_message,
            created_at=self.created_at,
            started_at=self.started_at,
            completed_at=self.completed_at
        )


class JobStats:
    """Statistics for job processing."""
    
    def __init__(self):
        self.total_jobs_processed: int = 0
        self.successful_jobs: int = 0
        self.failed_jobs: int = 0
        self.cancelled_jobs: int = 0
        self.total_bytes_processed: int = 0
        self.total_transcode_time: float = 0.0
        self.hw_accel_usage: Dict[str, int] = {}
        self.start_time: datetime = datetime.utcnow()
    
    def record_job_complete(self, job: Job, success: bool) -> None:
        """Record job completion stats."""
        self.total_jobs_processed += 1
        
        if job.status == JobStatus.CANCELLED:
            self.cancelled_jobs += 1
        elif success:
            self.successful_jobs += 1
        else:
            self.failed_jobs += 1
        
        if job.hw_accel_used:
            self.hw_accel_usage[job.hw_accel_used] = \
                self.hw_accel_usage.get(job.hw_accel_used, 0) + 1
        
        if job.started_at and job.completed_at:
            self.total_transcode_time += (job.completed_at - job.started_at).total_seconds()
    
    @property
    def average_transcode_speed(self) -> float:
        """Average transcoding speed (ratio of content time to transcode time)."""
        if self.total_transcode_time > 0 and self.successful_jobs > 0:
            return self.total_transcode_time / self.successful_jobs
        return 0.0
    
    @property
    def uptime_seconds(self) -> float:
        """Service uptime in seconds."""
        return (datetime.utcnow() - self.start_time).total_seconds()


class JobManager:
    """Manages the job queue and execution with proper lifecycle tracking."""
    
    def __init__(self, base_url: str = "http://localhost:8765"):
        self.config = get_config()
        self.jobs: Dict[str, Job] = {}
        self.queue: asyncio.Queue = asyncio.Queue()
        self.active_jobs: Dict[str, asyncio.Task] = {}
        self.engine = TranscodeEngine()
        self.stats = JobStats()
        self.base_url = base_url
        self.progress_callbacks: List[Callable[[str, TranscodeProgress], None]] = []
        self.status_callbacks: List[Callable[[str, JobStatus], None]] = []
        self._workers: List[asyncio.Task] = []
        self._cleanup_task: Optional[asyncio.Task] = None
        self._running = False
        
        # Cleanup settings
        self._cleanup_interval = 300  # 5 minutes
        self._job_ttl_streaming = 3600  # 1 hour for streaming jobs
        self._job_ttl_completed = self.config.transcoding.cleanup_after_hours * 3600
        
    async def start(self) -> None:
        """Start the job manager workers and cleanup task."""
        if self._running:
            return
        
        self._running = True
        max_workers = self.config.transcoding.max_concurrent_jobs
        
        # Clean up orphaned temp directories on startup
        await self._cleanup_orphaned_dirs()
        
        for i in range(max_workers):
            worker = asyncio.create_task(self._worker(i))
            self._workers.append(worker)
        
        # Start background cleanup task
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        
        logger.info(f"Started {max_workers} job workers + cleanup task")
    
    async def stop(self) -> None:
        """Stop the job manager and cancel all workers."""
        self._running = False
        
        # Cancel cleanup task
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None
        
        # Cancel all active jobs
        for job_id, task in self.active_jobs.items():
            if job_id in self.jobs:
                self.jobs[job_id].cancel_event.set()
            if task:
                task.cancel()
        
        # Cancel workers
        for worker in self._workers:
            worker.cancel()
        
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        
        # Final cleanup of all jobs
        await self._cleanup_all_jobs()
        
        logger.info("Job manager stopped")
    
    async def _worker(self, worker_id: int) -> None:
        """Worker coroutine that processes jobs from the queue."""
        logger.info(f"Worker {worker_id} started")
        
        while self._running:
            try:
                job_id = await asyncio.wait_for(self.queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            
            if job_id not in self.jobs:
                continue
            
            job = self.jobs[job_id]
            self.active_jobs[job_id] = None  # Track as active
            
            try:
                await self._process_job(job)
            except Exception as e:
                logger.exception(f"Worker {worker_id} error processing job {job_id}: {e}")
                job.status = JobStatus.ERROR
                job.error_message = str(e)
                job.completed_at = datetime.utcnow()
                self._notify_status(job_id, JobStatus.ERROR)
            finally:
                self.queue.task_done()
                if job_id in self.active_jobs:
                    del self.active_jobs[job_id]
                self.stats.record_job_complete(job, job.status == JobStatus.READY)
        
        logger.info(f"Worker {worker_id} stopped")
    
    async def _process_job(self, job: Job) -> None:
        """Process a single job."""
        job.status = JobStatus.PROCESSING
        job.started_at = datetime.utcnow()
        self._notify_status(job.id, JobStatus.PROCESSING)
        
        # Get media info for duration
        media_info = await self.engine.get_media_info(job.request.source)
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
            # Adaptive bitrate streaming
            success, result, hw_accel = await self.engine.transcode_abr(
                job_id=job.id,
                source=job.request.source,
                output_config=job.request.output,
                start_time=job.request.start_time,
                progress_callback=progress_callback,
                cancel_event=job.cancel_event
            )
        else:
            # Standard transcoding (stream or batch)
            success, result, hw_accel = await self.engine.transcode(
                job_id=job.id,
                source=job.request.source,
                mode=job.request.mode,
                output_config=job.request.output,
                start_time=job.request.start_time,
                progress_callback=progress_callback,
                cancel_event=job.cancel_event
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
            
            # Set URLs based on mode
            if job.request.mode == TranscodeMode.STREAM:
                job.stream_url = f"{self.base_url}/stream/{job.id}/master.m3u8"
            elif job.request.mode == TranscodeMode.ABR:
                job.stream_url = f"{self.base_url}/stream/{job.id}/master.m3u8"
            else:
                job.download_url = f"{self.base_url}/download/{job.id}"
            
            self._notify_status(job.id, JobStatus.READY)
            
            # Send callback if configured
            if job.request.callback_url:
                await self._send_callback(job)
        else:
            job.status = JobStatus.ERROR
            job.error_message = result
            self._notify_status(job.id, JobStatus.ERROR)
    
    async def _send_callback(self, job: Job) -> None:
        """Send callback to the configured URL."""
        if not job.request.callback_url:
            return
        
        import httpx
        
        try:
            async with httpx.AsyncClient() as client:
                await client.post(
                    job.request.callback_url,
                    json=job.to_response().model_dump(),
                    timeout=10.0
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
    
    async def create_job(self, request: TranscodeRequest) -> Job:
        """Create a new transcoding job."""
        job_id = str(uuid.uuid4())
        job = Job(id=job_id, request=request)
        
        self.jobs[job_id] = job
        await self.queue.put(job_id)
        
        logger.info(f"Created job {job_id} for source: {request.source}")
        return job
    
    def get_job(self, job_id: str, touch: bool = True) -> Optional[Job]:
        """Get a job by ID. Updates last_accessed if touch=True."""
        job = self.jobs.get(job_id)
        if job and touch:
            job.last_accessed = datetime.utcnow()
        return job
    
    async def cancel_job(self, job_id: str) -> bool:
        """Cancel a job."""
        job = self.jobs.get(job_id)
        if not job:
            return False
        
        if job.status in [JobStatus.READY, JobStatus.ERROR, JobStatus.CANCELLED]:
            return False
        
        job.cancel_event.set()
        job.status = JobStatus.CANCELLED
        job.completed_at = datetime.utcnow()
        
        # Clean up files
        self.engine.cleanup_job(job_id)
        
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
        """Update last_accessed time for a job (call when streaming segments)."""
        job = self.jobs.get(job_id)
        if job:
            job.last_accessed = datetime.utcnow()
    
    async def cleanup_job(self, job_id: str) -> bool:
        """Explicitly clean up a job's temp files and remove from tracking."""
        job = self.jobs.get(job_id)
        if not job:
            return False
        
        if not job.cleaned_up:
            self.engine.cleanup_job(job_id)
            job.cleaned_up = True
            logger.info(f"[Cleanup] Cleaned up job {job_id}")
        
        return True
    
    async def remove_job(self, job_id: str) -> bool:
        """Remove a job from tracking (after cleanup)."""
        job = self.jobs.get(job_id)
        if not job:
            return False
        
        # Clean up first if not already done
        if not job.cleaned_up:
            await self.cleanup_job(job_id)
        
        # Remove from jobs dict
        del self.jobs[job_id]
        logger.debug(f"[Cleanup] Removed job {job_id} from tracking")
        return True
    
    async def _cleanup_loop(self) -> None:
        """Background task that periodically cleans up stale jobs."""
        logger.info("[Cleanup] Starting cleanup loop")
        
        while self._running:
            try:
                await asyncio.sleep(self._cleanup_interval)
                await self._cleanup_stale_jobs()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[Cleanup] Error in cleanup loop: {e}")
        
        logger.info("[Cleanup] Cleanup loop stopped")
    
    async def _cleanup_stale_jobs(self) -> int:
        """Clean up jobs that haven't been accessed recently."""
        now = datetime.utcnow()
        cleaned = 0
        
        jobs_to_remove = []
        
        for job_id, job in list(self.jobs.items()):
            # Skip active/processing jobs
            if job.status in (JobStatus.QUEUED, JobStatus.PROCESSING):
                continue
            
            # Calculate age based on last access or completion
            if job.completed_at:
                age = (now - job.last_accessed).total_seconds()
                
                # Different TTL for streaming vs completed
                if job.request.mode in (TranscodeMode.STREAM, TranscodeMode.ABR):
                    ttl = self._job_ttl_streaming
                else:
                    ttl = self._job_ttl_completed
                
                if age > ttl:
                    jobs_to_remove.append(job_id)
        
        for job_id in jobs_to_remove:
            await self.cleanup_job(job_id)
            # Keep job metadata for a bit longer, just clean files
            cleaned += 1
        
        # Also remove very old job metadata (24h after cleanup)
        metadata_ttl = 86400  # 24 hours
        for job_id, job in list(self.jobs.items()):
            if job.cleaned_up and job.completed_at:
                age = (now - job.completed_at).total_seconds()
                if age > metadata_ttl:
                    del self.jobs[job_id]
        
        if cleaned > 0:
            logger.info(f"[Cleanup] Cleaned up {cleaned} stale job(s)")
        
        return cleaned
    
    async def _cleanup_orphaned_dirs(self) -> int:
        """Clean up temp directories that don't have a matching job (orphaned)."""
        temp_dir = Path(self.engine.temp_dir)
        if not temp_dir.exists():
            return 0
        
        cleaned = 0
        known_job_ids = set(self.jobs.keys())
        
        for item in temp_dir.iterdir():
            if item.is_dir():
                job_id = item.name
                if job_id not in known_job_ids:
                    # Orphaned directory - clean it up
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
    
    async def _cleanup_all_jobs(self) -> None:
        """Clean up all jobs (called on shutdown)."""
        logger.info(f"[Cleanup] Cleaning up {len(self.jobs)} job(s) on shutdown")
        
        for job_id in list(self.jobs.keys()):
            try:
                await self.cleanup_job(job_id)
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
                # Check if stale
                if job.completed_at:
                    age = (now - job.last_accessed).total_seconds()
                    ttl = self._job_ttl_streaming if job.request.mode in (TranscodeMode.STREAM, TranscodeMode.ABR) else self._job_ttl_completed
                    if age > ttl * 0.8:  # 80% of TTL = nearly stale
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

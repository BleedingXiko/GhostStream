"""
Job context and registry for transcoding operations.

Provides per-job state tracking and a registry for managing active jobs.
"""

import asyncio
import time
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List, Dict

logger = logging.getLogger(__name__)


@dataclass
class JobContext:
    """Per-job context for tracking state during transcoding."""
    job_id: str
    source: str
    job_dir: Path
    hw_fallback_attempted: bool = False
    start_time: float = field(default_factory=time.time)
    last_progress_time: float = field(default_factory=time.time)
    last_file_size: int = 0
    stderr_lines: List[str] = field(default_factory=list)
    stdout_bytes: int = 0
    retry_count: int = 0
    
    @property
    def log_prefix(self) -> str:
        """Get log prefix for this job (easier debugging in parallel transcodes)."""
        return f"[Job:{self.job_id[:8]}]"
    
    @property
    def elapsed_time(self) -> float:
        """Get elapsed time since job started."""
        return time.time() - self.start_time
    
    @property
    def time_since_progress(self) -> float:
        """Get time since last progress update."""
        return time.time() - self.last_progress_time
    
    def update_progress_time(self) -> None:
        """Update the last progress time to now."""
        self.last_progress_time = time.time()
    
    def reset_for_retry(self) -> None:
        """Reset context for a retry attempt."""
        self.retry_count += 1
        self.last_progress_time = time.time()
        self.last_file_size = 0
        self.stderr_lines.clear()
        self.stdout_bytes = 0


@dataclass
class JobRegistryEntry:
    """Entry in the job registry."""
    job_id: str
    source: str
    status: str  # 'queued', 'running', 'completed', 'failed', 'cancelled'
    start_time: float
    encoder: Optional[str] = None
    progress: float = 0.0
    variants: int = 1  # Number of ABR variants
    
    @property
    def elapsed_time(self) -> float:
        """Get elapsed time since job started."""
        return time.time() - self.start_time


class JobRegistry:
    """
    Registry to track active/queued jobs inside the engine.
    
    Thread-safe with async lock for concurrent access.
    """
    
    def __init__(self):
        self._jobs: Dict[str, JobRegistryEntry] = {}
        self._lock = asyncio.Lock()
    
    async def register(self, job_id: str, source: str) -> None:
        """Register a new job."""
        async with self._lock:
            self._jobs[job_id] = JobRegistryEntry(
                job_id=job_id,
                source=source,
                status="queued",
                start_time=time.time()
            )
    
    async def update_status(
        self,
        job_id: str,
        status: str,
        encoder: Optional[str] = None,
        progress: float = 0.0,
        variants: int = 1
    ) -> None:
        """Update job status and metadata."""
        async with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id].status = status
                if encoder:
                    self._jobs[job_id].encoder = encoder
                self._jobs[job_id].progress = progress
                self._jobs[job_id].variants = variants
    
    async def remove(self, job_id: str) -> None:
        """Remove a job from the registry."""
        async with self._lock:
            self._jobs.pop(job_id, None)
    
    async def get_active_jobs(self) -> List[JobRegistryEntry]:
        """Get list of active (queued/running) jobs."""
        async with self._lock:
            return [j for j in self._jobs.values() if j.status in ('queued', 'running')]
    
    async def get_job(self, job_id: str) -> Optional[JobRegistryEntry]:
        """Get a specific job by ID."""
        async with self._lock:
            return self._jobs.get(job_id)
    
    def get_active_count(self) -> int:
        """Non-async count for quick checks."""
        return sum(1 for j in self._jobs.values() if j.status == 'running')
    
    def get_running_jobs(self) -> List[JobRegistryEntry]:
        """Non-async get of running jobs."""
        return [j for j in self._jobs.values() if j.status == 'running']
    
    def get_total_variants(self) -> int:
        """Get total number of active encode streams (for NVENC limits)."""
        return sum(j.variants for j in self._jobs.values() if j.status == 'running')

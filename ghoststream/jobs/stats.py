"""
Job statistics tracking for GhostStream
"""

from datetime import datetime
from typing import Dict

from ..models import JobStatus
from .models import Job


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

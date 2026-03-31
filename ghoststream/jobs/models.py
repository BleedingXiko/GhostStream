"""
Job data models for GhostStream
"""

import gevent.event
from datetime import datetime
from typing import Optional
from dataclasses import dataclass, field

from ..models import (
    TranscodeRequest, TranscodeResponse, JobStatus, JobStatusResponse,
)


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
    control_token: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    last_accessed: datetime = field(default_factory=datetime.utcnow)
    cancel_event: gevent.event.Event = field(default_factory=gevent.event.Event)
    cleaned_up: bool = False
    # Stream sharing fields
    stream_key: Optional[str] = None  # Key for shared stream lookup
    viewer_count: int = 0  # Number of active viewers sharing this stream
    is_shared: bool = False  # Whether this job is being shared by multiple viewers
    
    def to_response(
        self,
        *,
        stream_url_override: Optional[str] = None,
        download_url_override: Optional[str] = None,
        control_token: Optional[str] = None,
    ) -> TranscodeResponse:
        return TranscodeResponse(
            job_id=self.id,
            status=self.status,
            progress=self.progress,
            stream_url=stream_url_override or self.stream_url,
            control_token=control_token,
            download_url=download_url_override or self.download_url,
            duration=self.duration,
            eta_seconds=self.eta_seconds,
            hw_accel_used=self.hw_accel_used,
            error_message=self.error_message,
            # Stream sharing info - critical for progress tracking
            start_time=self.request.start_time,
            is_shared=self.is_shared,
            viewer_count=self.viewer_count
        )
    
    def to_status_response(
        self,
        *,
        stream_url_override: Optional[str] = None,
        download_url_override: Optional[str] = None,
        control_token: Optional[str] = None,
    ) -> JobStatusResponse:
        return JobStatusResponse(
            job_id=self.id,
            status=self.status,
            progress=self.progress,
            current_time=self.current_time,
            duration=self.duration,
            stream_url=stream_url_override or self.stream_url,
            download_url=download_url_override or self.download_url,
            control_token=control_token,
            eta_seconds=self.eta_seconds,
            hw_accel_used=self.hw_accel_used,
            error_message=self.error_message,
            created_at=self.created_at,
            started_at=self.started_at,
            completed_at=self.completed_at,
            # Stream sharing info - critical for progress tracking
            start_time=self.request.start_time,
            is_shared=self.is_shared,
            viewer_count=self.viewer_count
        )

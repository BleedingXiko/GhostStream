"""
Transcode API routes for GhostStream
"""

from typing import Optional
from fastapi import APIRouter, HTTPException

from ...models import (
    TranscodeRequest, TranscodeResponse, JobStatusResponse, JobStatus
)
from ...jobs import get_job_manager

router = APIRouter()


@router.post("/api/transcode/start", response_model=TranscodeResponse)
async def start_transcode(request: TranscodeRequest):
    """Start a new transcoding job or join existing shared stream."""
    job_manager = get_job_manager()
    
    # Validate source URL
    if not request.source:
        raise HTTPException(status_code=400, detail="Source URL is required")
    
    # Create job or get existing shared stream (session_id now in request body)
    job = await job_manager.create_job(request, request.session_id)
    
    return job.to_response()


@router.get("/api/transcode/{job_id}/status", response_model=JobStatusResponse)
async def get_job_status(job_id: str):
    """Get the status of a transcoding job."""
    job_manager = get_job_manager()
    job = job_manager.get_job(job_id)
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    return job.to_status_response()


@router.post("/api/transcode/{job_id}/cancel")
async def cancel_job(job_id: str):
    """Cancel a transcoding job."""
    job_manager = get_job_manager()
    
    success = await job_manager.cancel_job(job_id)
    
    if not success:
        raise HTTPException(status_code=400, detail="Job cannot be cancelled")
    
    return {"status": "cancelled", "job_id": job_id}


@router.get("/api/transcode/{job_id}/stream")
async def get_stream_info(job_id: str):
    """Get stream information for a job."""
    job_manager = get_job_manager()
    job = job_manager.get_job(job_id)
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    if job.status != JobStatus.READY and job.status != JobStatus.PROCESSING:
        raise HTTPException(status_code=400, detail=f"Job is not ready for streaming: {job.status.value}")
    
    return {
        "job_id": job_id,
        "stream_url": job.stream_url,
        "status": job.status.value
    }


@router.delete("/api/transcode/{job_id}")
async def delete_job(job_id: str):
    """Delete a job and clean up its temp files."""
    job_manager = get_job_manager()
    job = job_manager.get_job(job_id, touch=False)
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Cancel if still running
    if job.status in [JobStatus.QUEUED, JobStatus.PROCESSING]:
        await job_manager.cancel_job(job_id)
    
    # Clean up and remove
    await job_manager.remove_job(job_id)
    
    return {"status": "deleted", "job_id": job_id}


@router.get("/api/cleanup/stats")
async def get_cleanup_stats():
    """Get statistics about job cleanup and temp files."""
    job_manager = get_job_manager()
    return job_manager.get_cleanup_stats()


@router.post("/api/cleanup/run")
async def run_cleanup():
    """Manually trigger cleanup of stale jobs."""
    job_manager = get_job_manager()
    
    cleaned = await job_manager._cleanup_stale_jobs()
    orphaned = await job_manager._cleanup_orphaned_dirs()
    
    return {
        "stale_jobs_cleaned": cleaned,
        "orphaned_dirs_cleaned": orphaned
    }


@router.get("/api/streams/shared")
async def get_shared_streams():
    """Get statistics about shared HLS streams."""
    job_manager = get_job_manager()
    return job_manager.get_shared_stream_stats()


@router.post("/api/transcode/{job_id}/leave")
async def leave_stream(job_id: str, session_id: Optional[str] = None):
    """
    Notify that a viewer is leaving a shared stream.
    This decrements the viewer count for the stream.
    """
    job_manager = get_job_manager()
    job = job_manager.get_job(job_id, touch=False)
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    has_viewers = await job_manager.leave_stream(job_id, session_id)
    
    return {
        "job_id": job_id,
        "viewers_remaining": job.viewer_count,
        "is_shared": job.is_shared
    }


# GhostHub compatibility routes (without /api/ prefix)
@router.post("/transcode", response_model=TranscodeResponse)
async def start_transcode_compat(request: TranscodeRequest):
    """Start transcode (GhostHub compatibility)."""
    return await start_transcode(request)


@router.get("/transcode/{job_id}", response_model=JobStatusResponse)
async def get_status_compat(job_id: str):
    """Get job status (GhostHub compatibility)."""
    return await get_job_status(job_id)


@router.delete("/transcode/{job_id}")
async def cancel_job_compat(job_id: str):
    """Cancel job (GhostHub compatibility)."""
    return await cancel_job(job_id)

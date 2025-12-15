"""
Health and stats API routes for GhostStream
"""

import time
from fastapi import APIRouter

from ... import __version__
from ...config import get_config
from ...hardware import get_capabilities
from ...models import HealthResponse, CapabilitiesResponse, StatsResponse
from ...jobs import get_job_manager

router = APIRouter()

# Start time - set by lifespan
start_time: float = 0


def set_start_time(t: float) -> None:
    """Set the server start time."""
    global start_time
    start_time = t


@router.get("/api/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint."""
    job_manager = get_job_manager()
    
    return HealthResponse(
        status="healthy",
        version=__version__,
        uptime_seconds=time.time() - start_time,
        current_jobs=job_manager.get_active_count(),
        queued_jobs=job_manager.get_queue_length()
    )


@router.get("/api/capabilities", response_model=CapabilitiesResponse)
async def get_capabilities_endpoint():
    """Get transcoding capabilities."""
    config = get_config()
    capabilities = get_capabilities(
        config.transcoding.ffmpeg_path,
        config.transcoding.max_concurrent_jobs,
        force_refresh=False
    )
    
    return CapabilitiesResponse(**capabilities.to_dict())


@router.get("/api/stats", response_model=StatsResponse)
async def get_stats():
    """Get service statistics."""
    job_manager = get_job_manager()
    stats = job_manager.stats
    
    return StatsResponse(
        total_jobs_processed=stats.total_jobs_processed,
        successful_jobs=stats.successful_jobs,
        failed_jobs=stats.failed_jobs,
        cancelled_jobs=stats.cancelled_jobs,
        current_queue_length=job_manager.get_queue_length(),
        active_jobs=job_manager.get_active_count(),
        average_transcode_speed=stats.average_transcode_speed,
        total_bytes_processed=stats.total_bytes_processed,
        uptime_seconds=stats.uptime_seconds,
        hw_accel_usage=stats.hw_accel_usage
    )


# GhostHub compatibility routes (without /api/ prefix)
@router.get("/health", response_model=HealthResponse)
async def health_check_compat():
    """Health check endpoint (GhostHub compatibility)."""
    return await health_check()


@router.get("/capabilities", response_model=CapabilitiesResponse)
async def capabilities_compat():
    """Capabilities endpoint (GhostHub compatibility)."""
    return await get_capabilities_endpoint()

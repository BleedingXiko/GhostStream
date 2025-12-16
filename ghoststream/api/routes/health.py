"""
Health and stats API routes for GhostStream
Enterprise-grade health checks with detailed system status
"""

import os
import time
from typing import Dict, Any
from fastapi import APIRouter
from fastapi.responses import JSONResponse

import psutil

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


@router.get("/api/health/detailed", tags=["Health"])
async def detailed_health_check():
    """
    Detailed health check with system metrics.
    Enterprise endpoint for monitoring systems.
    """
    job_manager = get_job_manager()
    config = get_config()
    
    # System metrics
    cpu_percent = psutil.cpu_percent(interval=0.1)
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage(config.transcoding.temp_directory)
    
    # Determine overall health status
    status = "healthy"
    checks: Dict[str, Any] = {}
    
    # CPU check
    checks["cpu"] = {
        "status": "healthy" if cpu_percent < 90 else "degraded",
        "usage_percent": cpu_percent
    }
    if cpu_percent >= 95:
        status = "unhealthy"
    elif cpu_percent >= 90:
        status = "degraded"
    
    # Memory check
    checks["memory"] = {
        "status": "healthy" if memory.percent < 85 else "degraded",
        "usage_percent": memory.percent,
        "available_mb": memory.available // (1024 * 1024)
    }
    if memory.percent >= 95:
        status = "unhealthy"
    elif memory.percent >= 85 and status == "healthy":
        status = "degraded"
    
    # Disk check
    checks["disk"] = {
        "status": "healthy" if disk.percent < 85 else "degraded",
        "usage_percent": disk.percent,
        "free_gb": disk.free // (1024 * 1024 * 1024)
    }
    if disk.percent >= 95:
        status = "unhealthy"
    elif disk.percent >= 85 and status == "healthy":
        status = "degraded"
    
    # Job queue check
    queue_length = job_manager.get_queue_length()
    max_queue = config.transcoding.max_queue_size
    queue_percent = (queue_length / max_queue) * 100 if max_queue > 0 else 0
    checks["job_queue"] = {
        "status": "healthy" if queue_percent < 80 else "degraded",
        "current": queue_length,
        "max": max_queue,
        "usage_percent": queue_percent
    }
    if queue_percent >= 95 and status == "healthy":
        status = "degraded"
    
    # Active jobs
    checks["active_jobs"] = {
        "status": "healthy",
        "count": job_manager.get_active_count(),
        "max_concurrent": config.transcoding.max_concurrent_jobs
    }
    
    response_status = 200 if status == "healthy" else (503 if status == "unhealthy" else 200)
    
    return JSONResponse(
        status_code=response_status,
        content={
            "status": status,
            "version": __version__,
            "environment": os.environ.get("GHOSTSTREAM_ENV", "development"),
            "uptime_seconds": time.time() - start_time,
            "checks": checks,
            "timestamp": time.time()
        }
    )


@router.get("/api/ready", tags=["Health"])
async def readiness_check():
    """
    Kubernetes readiness probe endpoint.
    Returns 200 if service is ready to accept traffic.
    """
    job_manager = get_job_manager()
    config = get_config()
    
    # Check if queue is not full
    queue_length = job_manager.get_queue_length()
    max_queue = config.transcoding.max_queue_size
    
    if queue_length >= max_queue:
        return JSONResponse(
            status_code=503,
            content={"ready": False, "reason": "Queue full"}
        )
    
    return {"ready": True}


@router.get("/api/live", tags=["Health"])
async def liveness_check():
    """
    Kubernetes liveness probe endpoint.
    Returns 200 if service is alive.
    """
    return {"alive": True, "timestamp": time.time()}


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

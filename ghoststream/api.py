"""
FastAPI application and API endpoints for GhostStream
"""

import asyncio
import logging
import time
import os
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Request, Response
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import __version__
from .config import get_config, load_config, set_config
from .hardware import get_capabilities
from .models import (
    TranscodeRequest, TranscodeResponse, JobStatusResponse,
    HealthResponse, StatsResponse, CapabilitiesResponse,
    WebSocketMessage, JobStatus
)
from .jobs import get_job_manager, set_job_manager, JobManager
from .transcoder import TranscodeProgress
from .discovery import GhostStreamService, GhostHubRegistration

logger = logging.getLogger(__name__)

# Global state
start_time: float = 0
mdns_service: Optional[GhostStreamService] = None
ghosthub_registration: Optional[GhostHubRegistration] = None
websocket_connections: List[WebSocket] = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    global start_time, mdns_service, ghosthub_registration
    
    start_time = time.time()
    config = get_config()
    
    # Determine base URL
    host = config.server.host
    port = config.server.port
    if host == "0.0.0.0":
        import socket
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            host = s.getsockname()[0]
            s.close()
        except:
            host = "127.0.0.1"
    
    base_url = f"http://{host}:{port}"
    
    # Initialize job manager
    job_manager = JobManager(base_url=base_url)
    set_job_manager(job_manager)
    
    # Register WebSocket callbacks
    job_manager.register_progress_callback(broadcast_progress)
    job_manager.register_status_callback(broadcast_status)
    
    # Start job manager
    await job_manager.start()
    
    # Start mDNS service
    mdns_service = GhostStreamService(config.server.host, config.server.port)
    mdns_service.start()
    
    # Start UDP broadcast responder as fallback discovery
    mdns_service.start_udp_responder()
    
    # Start GhostHub registration if configured
    if config.ghosthub.url and config.ghosthub.auto_register:
        ghosthub_registration = GhostHubRegistration(
            ghosthub_url=config.ghosthub.url,
            port=config.server.port
        )
        asyncio.create_task(
            ghosthub_registration.start_periodic_registration(
                interval_seconds=config.ghosthub.register_interval_seconds
            )
        )
    
    # Ensure temp directory exists
    temp_dir = Path(config.transcoding.temp_directory)
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    logger.info(f"GhostStream v{__version__} started on {base_url}")
    
    yield
    
    # Shutdown
    logger.info("Shutting down GhostStream...")
    
    if mdns_service:
        mdns_service.stop()
    
    if ghosthub_registration:
        ghosthub_registration.stop()
    
    await job_manager.stop()
    
    logger.info("GhostStream shutdown complete")


# Create FastAPI app
app = FastAPI(
    title="GhostStream",
    description="Open Source Cross-Platform Transcoding Service",
    version=__version__,
    lifespan=lifespan
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def broadcast_progress(job_id: str, progress: TranscodeProgress) -> None:
    """Broadcast progress update to all WebSocket clients."""
    message = {
        "type": "progress",
        "job_id": job_id,
        "data": {
            "progress": progress.percent,
            "frame": progress.frame,
            "fps": progress.fps,
            "time": progress.time,
            "speed": progress.speed
        }
    }
    asyncio.create_task(_broadcast_message(message))


def broadcast_status(job_id: str, status: JobStatus) -> None:
    """Broadcast status change to all WebSocket clients."""
    message = {
        "type": "status_change",
        "job_id": job_id,
        "data": {
            "status": status.value
        }
    }
    asyncio.create_task(_broadcast_message(message))


async def _broadcast_message(message: dict) -> None:
    """Send message to all connected WebSocket clients."""
    disconnected = []
    for ws in websocket_connections[:]:  # Iterate over a copy
        try:
            await ws.send_json(message)
        except Exception:
            disconnected.append(ws)
    
    for ws in disconnected:
        if ws in websocket_connections:
            websocket_connections.remove(ws)


# ============== API Endpoints ==============

@app.get("/api/health", response_model=HealthResponse)
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


@app.get("/api/capabilities", response_model=CapabilitiesResponse)
async def get_capabilities_endpoint():
    """Get transcoding capabilities."""
    config = get_config()
    capabilities = get_capabilities(
        config.transcoding.ffmpeg_path,
        config.transcoding.max_concurrent_jobs,
        force_refresh=False
    )
    
    return CapabilitiesResponse(**capabilities.to_dict())


@app.get("/api/stats", response_model=StatsResponse)
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


@app.post("/api/transcode/start", response_model=TranscodeResponse)
async def start_transcode(request: TranscodeRequest):
    """Start a new transcoding job."""
    job_manager = get_job_manager()
    
    # Validate source URL
    if not request.source:
        raise HTTPException(status_code=400, detail="Source URL is required")
    
    # Create job
    job = await job_manager.create_job(request)
    
    return job.to_response()


@app.get("/api/transcode/{job_id}/status", response_model=JobStatusResponse)
async def get_job_status(job_id: str):
    """Get the status of a transcoding job."""
    job_manager = get_job_manager()
    job = job_manager.get_job(job_id)
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    return job.to_status_response()


@app.post("/api/transcode/{job_id}/cancel")
async def cancel_job(job_id: str):
    """Cancel a transcoding job."""
    job_manager = get_job_manager()
    
    success = await job_manager.cancel_job(job_id)
    
    if not success:
        raise HTTPException(status_code=400, detail="Job cannot be cancelled")
    
    return {"status": "cancelled", "job_id": job_id}


@app.get("/api/transcode/{job_id}/stream")
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


@app.delete("/api/transcode/{job_id}")
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


@app.get("/api/cleanup/stats")
async def get_cleanup_stats():
    """Get statistics about job cleanup and temp files."""
    job_manager = get_job_manager()
    return job_manager.get_cleanup_stats()


@app.post("/api/cleanup/run")
async def run_cleanup():
    """Manually trigger cleanup of stale jobs."""
    job_manager = get_job_manager()
    
    cleaned = await job_manager._cleanup_stale_jobs()
    orphaned = await job_manager._cleanup_orphaned_dirs()
    
    return {
        "stale_jobs_cleaned": cleaned,
        "orphaned_dirs_cleaned": orphaned
    }


@app.get("/stream/{job_id}/{filename:path}")
async def stream_file(job_id: str, filename: str, request: Request):
    """Serve HLS stream files."""
    job_manager = get_job_manager()
    
    # Touch job to keep it alive while streaming
    job_manager.touch_job(job_id)
    
    config = get_config()
    temp_dir = Path(config.transcoding.temp_directory)
    file_path = temp_dir / job_id / filename
    
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Stream file not found")
    
    # Determine content type
    if filename.endswith(".m3u8"):
        media_type = "application/vnd.apple.mpegurl"
    elif filename.endswith(".ts"):
        media_type = "video/mp2t"
    elif filename.endswith(".mp4"):
        media_type = "video/mp4"
    else:
        media_type = "application/octet-stream"
    
    # Handle range requests for seeking
    file_size = file_path.stat().st_size
    range_header = request.headers.get("range")
    
    if range_header:
        # Parse range header
        range_match = range_header.replace("bytes=", "").split("-")
        start = int(range_match[0]) if range_match[0] else 0
        end = int(range_match[1]) if range_match[1] else file_size - 1
        
        if start >= file_size:
            raise HTTPException(status_code=416, detail="Range not satisfiable")
        
        end = min(end, file_size - 1)
        content_length = end - start + 1
        
        def file_iterator():
            with open(file_path, "rb") as f:
                f.seek(start)
                remaining = content_length
                while remaining > 0:
                    chunk_size = min(8192, remaining)
                    data = f.read(chunk_size)
                    if not data:
                        break
                    remaining -= len(data)
                    yield data
        
        headers = {
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(content_length),
            "Content-Type": media_type,
        }
        
        return StreamingResponse(
            file_iterator(),
            status_code=206,
            headers=headers,
            media_type=media_type
        )
    
    return FileResponse(
        file_path,
        media_type=media_type,
        headers={"Accept-Ranges": "bytes"}
    )


@app.get("/download/{job_id}")
async def download_file(job_id: str):
    """Download completed batch transcode file."""
    job_manager = get_job_manager()
    job = job_manager.get_job(job_id)
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    if job.status != JobStatus.READY:
        raise HTTPException(status_code=400, detail="Job is not ready for download")
    
    if not job.output_path or not Path(job.output_path).exists():
        raise HTTPException(status_code=404, detail="Output file not found")
    
    return FileResponse(
        job.output_path,
        filename=Path(job.output_path).name,
        media_type="application/octet-stream"
    )


# ============== WebSocket ==============

@app.websocket("/ws/progress")
async def websocket_progress(websocket: WebSocket):
    """WebSocket endpoint for real-time progress updates."""
    await websocket.accept()
    websocket_connections.append(websocket)
    
    logger.info(f"WebSocket client connected. Total connections: {len(websocket_connections)}")
    
    try:
        while True:
            # Keep connection alive and handle incoming messages
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                
                # Handle subscription messages
                import json
                try:
                    message = json.loads(data)
                    if message.get("type") == "ping":
                        await websocket.send_json({"type": "pong"})
                except json.JSONDecodeError:
                    pass
                    
            except asyncio.TimeoutError:
                # Send ping to keep connection alive
                try:
                    await websocket.send_json({"type": "ping"})
                except:
                    break
                    
    except WebSocketDisconnect:
        pass
    finally:
        if websocket in websocket_connections:
            websocket_connections.remove(websocket)
        logger.info(f"WebSocket client disconnected. Total connections: {len(websocket_connections)}")


# ============== GhostHub Compatibility Routes ==============
# These routes match what GhostHub expects (without /api/ prefix)

@app.get("/health", response_model=HealthResponse)
async def health_check_compat():
    """Health check endpoint (GhostHub compatibility)."""
    return await health_check()


@app.get("/capabilities", response_model=CapabilitiesResponse)
async def capabilities_compat():
    """Capabilities endpoint (GhostHub compatibility)."""
    return await get_capabilities_endpoint()


@app.post("/transcode", response_model=TranscodeResponse)
async def start_transcode_compat(request: TranscodeRequest):
    """Start transcode (GhostHub compatibility)."""
    return await start_transcode(request)


@app.get("/transcode/{job_id}", response_model=JobStatusResponse)
async def get_status_compat(job_id: str):
    """Get job status (GhostHub compatibility)."""
    return await get_job_status(job_id)


@app.delete("/transcode/{job_id}")
async def cancel_job_compat(job_id: str):
    """Cancel job (GhostHub compatibility)."""
    return await cancel_job(job_id)


# ============== API Key Middleware ==============

@app.middleware("http")
async def api_key_middleware(request: Request, call_next):
    """Check API key if configured."""
    config = get_config()
    api_key = config.security.api_key
    
    # Skip auth for health check
    if request.url.path in ("/api/health", "/health"):
        return await call_next(request)
    
    # Skip if no API key configured
    if not api_key:
        return await call_next(request)
    
    # Check API key
    request_key = request.headers.get("X-API-Key") or request.query_params.get("api_key")
    
    if request_key != api_key:
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid or missing API key"}
        )
    
    return await call_next(request)

"""
FastAPI application and lifespan management for GhostStream
"""

import asyncio
import logging
import time
import socket
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from .. import __version__
from ..config import get_config
from ..discovery import GhostStreamService, GhostHubRegistration
from ..jobs import JobManager, set_job_manager

from .routes import health_router, transcode_router, stream_router, set_start_time
from .websocket import websocket_progress_handler, broadcast_progress, broadcast_status
from .middleware import api_key_middleware

logger = logging.getLogger(__name__)

# Global state
mdns_service = None
ghosthub_registration = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    global mdns_service, ghosthub_registration
    
    set_start_time(time.time())
    config = get_config()
    
    # Determine base URL
    host = config.server.host
    port = config.server.port
    if host == "0.0.0.0":
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


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
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
    
    # Add API key middleware
    app.middleware("http")(api_key_middleware)
    
    # Include routers
    app.include_router(health_router)
    app.include_router(transcode_router)
    app.include_router(stream_router)
    
    # WebSocket endpoint
    @app.websocket("/ws/progress")
    async def websocket_progress(websocket: WebSocket):
        """WebSocket endpoint for real-time progress updates."""
        await websocket_progress_handler(websocket)
    
    return app


# Default app instance
app = create_app()

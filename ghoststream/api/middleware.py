"""
API middleware for GhostStream
"""

import secrets
from fastapi import Request
from fastapi.responses import JSONResponse

from ..config import get_config
from .websocket import get_websocket_manager


async def api_key_middleware(request: Request, call_next):
    """Check API key if configured."""
    config = get_config()
    api_key = config.security.api_key
    
    # Track HTTP client activity (for GUI "connected" state)
    # Skip internal paths and WebSocket upgrades
    if not request.url.path.startswith("/ws"):
        client_ip = request.client.host if request.client else None
        if client_ip and client_ip not in ("127.0.0.1", "localhost"):
            ws_manager = get_websocket_manager()
            await ws_manager.track_http_client(client_ip)
    
    # Skip auth for health check
    if request.url.path in ("/api/health", "/health"):
        return await call_next(request)
    
    # Skip if no API key configured
    if not api_key:
        return await call_next(request)
    
    # Check API key using constant-time comparison to prevent timing attacks
    request_key = request.headers.get("X-API-Key") or request.query_params.get("api_key")
    
    if not request_key or not secrets.compare_digest(request_key, api_key):
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid or missing API key"}
        )
    
    return await call_next(request)

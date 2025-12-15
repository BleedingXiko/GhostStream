"""
API middleware for GhostStream
"""

from fastapi import Request
from fastapi.responses import JSONResponse

from ..config import get_config


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

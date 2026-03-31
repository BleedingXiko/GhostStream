"""
Flask middleware for GhostStream — Specter-native.
"""

import secrets
from functools import wraps

from flask import request, jsonify, make_response

from ..config import get_config


def cors_after_request(response):
    """CORS headers — allow all for local network use."""
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "*"
    response.headers["Access-Control-Allow-Credentials"] = "true"
    return response


def api_key_before_request():
    """Check API key if configured."""
    config = get_config()
    api_key = config.security.api_key

    # Skip auth for health check
    if request.path in ("/api/health", "/health"):
        return None

    # Skip if no API key configured
    if not api_key:
        return None

    # Check API key using constant-time comparison
    request_key = (
        request.headers.get("X-API-Key")
        or request.args.get("api_key")
    )

    if not request_key or not secrets.compare_digest(request_key, api_key):
        return jsonify({"detail": "Invalid or missing API key"}), 401

    return None

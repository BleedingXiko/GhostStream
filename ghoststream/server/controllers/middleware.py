"""Ingress middleware for GhostStream HTTP requests."""

from __future__ import annotations

import secrets

from flask import jsonify, request
from specter.core.registry import registry

from ...app.registry_keys import APP_CONFIG
from ...contracts.security import API_KEY_HEADER, API_KEY_QUERY_PARAM


def cors_after_request(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "*"
    response.headers["Access-Control-Allow-Credentials"] = "true"
    return response


def api_key_before_request():
    config = registry.require(APP_CONFIG)
    api_key = config.security.api_key

    if request.path in ("/api/health", "/health"):
        return None

    if not api_key:
        return None

    request_key = request.headers.get(API_KEY_HEADER) or request.args.get(API_KEY_QUERY_PARAM)
    if not request_key or not secrets.compare_digest(request_key, api_key):
        return jsonify({"detail": "Invalid or missing API key"}), 401

    return None

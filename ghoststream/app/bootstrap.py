"""Bootstrap helpers for explicit runtime construction."""

from __future__ import annotations

import socket


def resolve_bind_host(host: str) -> str:
    if host != "0.0.0.0":
        return host
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        detected = sock.getsockname()[0]
        sock.close()
        return detected
    except Exception:
        return "127.0.0.1"


def determine_base_url(config) -> str:
    if config.server.advertised_url:
        return config.server.advertised_url.rstrip("/")
    host = resolve_bind_host(config.server.host)
    return f"http://{host}:{config.server.port}"

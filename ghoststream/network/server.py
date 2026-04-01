"""
Specter-owned HTTP ingress — NetworkIngressController.

Flask + gevent-websocket. No aiohttp, no asyncio.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from flask import Flask
from geventwebsocket.handler import WebSocketHandler
from geventwebsocket.resource import Resource, WebSocketApplication
from geventwebsocket.websocket import WebSocket
from gevent.pywsgi import WSGIServer

from ..config import get_config
from ..specter.core.lifecycle import Service
from .handlers import register_routes
from .middleware import cors_after_request, api_key_before_request
from .websocket import get_websocket_manager

logger = logging.getLogger(__name__)


class ProgressWebSocketApplication(WebSocketApplication):
    """Route `/ws/progress` through geventwebsocket's native app layer."""

    controller: Optional["NetworkIngressController"] = None

    def handle(self):
        if self.controller is None:
            self.ws.close()
            return
        self.controller._handle_websocket(self.ws)


class NetworkIngressController(Service):
    """Specter-owned HTTP/HLS/WebSocket ingress via Flask + gevent."""

    def __init__(self) -> None:
        super().__init__("network-ingress")
        config = get_config()
        self._host = config.server.host
        self._port = config.server.port

        self._app = Flask("ghoststream")
        self._app.after_request(cors_after_request)
        self._app.before_request(api_key_before_request)

        register_routes(self._app)
        ProgressWebSocketApplication.controller = self
        self._wsgi_app = Resource([
            (r"^/ws/progress$", ProgressWebSocketApplication),
            (r"^/.*", self._app),
        ])

        self._server = None

    def _handle_websocket(self, ws: WebSocket) -> None:
        """Handle a WebSocket connection lifecycle."""
        manager = get_websocket_manager()
        conn = manager.connect(ws)
        if not conn:
            return

        try:
            while not ws.closed:
                message = ws.receive()
                if message is None:
                    break
                if isinstance(message, str):
                    manager.handle_message(conn, message)
        except Exception as e:
            logger.debug(f"[WS:{conn.id}] Connection error: {e}")
        finally:
            manager.disconnect(conn)

    def on_start(self) -> None:
        self._server = WSGIServer(
            (self._host, self._port),
            self._wsgi_app,
            handler_class=WebSocketHandler,
            log=None,  # Suppress default gevent access logs
            error_log=logger,  # Route errors through standard logging to protect TUI
        )
        self._server.start()
        logger.info("Network ingress listening on %s:%s", self._host, self._port)

    def on_stop(self) -> None:
        if self._server:
            self._server.stop(timeout=5)
        logger.info("Network ingress stopped")

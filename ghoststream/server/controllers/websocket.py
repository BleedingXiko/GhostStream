"""Specter-owned HTTP and websocket ingress controller for GhostStream."""

from __future__ import annotations

import logging
from typing import Optional

from flask import Flask, request
from gevent.pywsgi import WSGIServer
from geventwebsocket.handler import WebSocketHandler
from geventwebsocket.resource import Resource, WebSocketApplication
from geventwebsocket.websocket import WebSocket

from ...app.registry_keys import CLIENT_PRESENCE
from ...specter.core.controller import Controller
from ...specter.core.lifecycle import Service
from ...specter.core.registry import registry
from ..services.websocket import WebSocketManager
from .middleware import api_key_before_request, cors_after_request

logger = logging.getLogger(__name__)
INTERNAL_DASHBOARD_HEADER = "X-GhostStream-Internal"


def extract_http_client_name() -> Optional[str]:
    if request.headers.get(INTERNAL_DASHBOARD_HEADER) == "tui-dashboard":
        return None

    name = (
        request.headers.get("X-GhostStream-Client")
        or request.headers.get("X-GhostStream-Client-Name")
        or request.headers.get("X-Client-Name")
        or request.headers.get("X-Client")
        or request.headers.get("Client-Name")
        or request.headers.get("Client")
        or request.args.get("client_name")
        or request.args.get("clientName")
        or request.args.get("client")
    )
    if not name and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        payload = request.get_json(silent=True) or {}
        if isinstance(payload, dict):
            name = (
                payload.get("client_name")
                or payload.get("clientName")
                or payload.get("client")
            )
    if isinstance(name, str):
        name = name.strip()
    if name:
        return name

    session_id = request.args.get("session_id")
    if not session_id and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        payload = request.get_json(silent=True) or {}
        if isinstance(payload, dict):
            session_id = payload.get("session_id")
    if isinstance(session_id, str) and session_id.strip():
        return f"session:{session_id.strip()}"

    forwarded_for = request.headers.get("X-Forwarded-For")
    if isinstance(forwarded_for, str) and forwarded_for.strip():
        return forwarded_for.split(",")[0].strip()

    remote_addr = request.remote_addr
    if isinstance(remote_addr, str) and remote_addr.strip():
        return remote_addr.strip()

    return None


def track_http_client_header() -> Optional[str]:
    name = extract_http_client_name()
    if name:
        registry.require(CLIENT_PRESENCE).seen(name)
    return name


def configure_http_app(app: Flask, controllers: tuple[Controller, ...]) -> Flask:
    app.after_request(cors_after_request)
    app.before_request(api_key_before_request)

    for controller in controllers:
        app.register_blueprint(controller.build_blueprint())

    @app.before_request
    def _track_http_client():
        track_http_client_header()

    return app


class ProgressWebSocketApplication(WebSocketApplication):
    controller: Optional["GhostStreamIngressController"] = None

    def handle(self):
        if self.controller is None:
            self.ws.close()
            return
        self.controller._handle_websocket(self.ws)


class GhostStreamIngressController(Service):
    def __init__(
        self,
        *,
        host: str,
        port: int,
        websocket_manager: WebSocketManager,
        controllers: tuple[Controller, ...],
    ) -> None:
        super().__init__("http-ingress")
        self._host = host
        self._port = port
        self._websocket_manager = websocket_manager
        self._controllers = controllers

        self._app = configure_http_app(Flask("ghoststream"), self._controllers)
        ProgressWebSocketApplication.controller = self
        self._wsgi_app = Resource(
            [
                (r"^/ws/progress$", ProgressWebSocketApplication),
                (r"^/.*", self._app),
            ]
        )

        self._server = None

    def _handle_websocket(self, ws: WebSocket) -> None:
        manager = self._websocket_manager
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
        except Exception as exc:
            logger.debug("[WS:%s] Connection error: %s", conn.id, exc)
        finally:
            manager.disconnect(conn)

    def on_start(self) -> None:
        for controller in self._controllers:
            controller.start()

        self._server = WSGIServer(
            (self._host, self._port),
            self._wsgi_app,
            handler_class=WebSocketHandler,
            log=None,
            error_log=logger,
        )
        self._server.start()
        logger.info("Network ingress listening on %s:%s", self._host, self._port)

    def on_stop(self) -> None:
        if self._server:
            self._server.stop(timeout=5)
        for controller in reversed(self._controllers):
            controller.stop()
        logger.info("Network ingress stopped")

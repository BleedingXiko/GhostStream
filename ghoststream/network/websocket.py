"""
WebSocket manager for GhostStream — Specter-native (gevent-websocket).

Uses gevent primitives instead of asyncio for connection management.
"""

import json
import logging
import time
import uuid
import gevent
import gevent.queue
import gevent.lock
import gevent.event
from dataclasses import dataclass, field
from typing import Dict, Set, Optional, Any
from enum import Enum

from ..models import JobStatus
from ..security import CapabilityError, CapabilityService, get_capability_service
from ..transcoding import TranscodeProgress

logger = logging.getLogger(__name__)


class ConnectionState(Enum):
    CONNECTING = "connecting"
    CONNECTED = "connected"
    CLOSING = "closing"
    CLOSED = "closed"


@dataclass
class WebSocketConnection:
    """Represents a single WebSocket connection with its state."""
    id: str
    ws: Any  # gevent-websocket WebSocket object
    state: ConnectionState = ConnectionState.CONNECTING
    subscribed_jobs: Set[str] = field(default_factory=set)
    authorized_jobs: Set[str] = field(default_factory=set)
    subscribe_all: bool = False
    created_at: float = field(default_factory=time.time)
    last_ping: float = field(default_factory=time.time)
    last_pong: float = field(default_factory=time.time)
    message_queue: gevent.queue.Queue = field(default_factory=lambda: gevent.queue.Queue(maxsize=100))
    send_greenlet: Optional[gevent.Greenlet] = None
    missed_pongs: int = 0

    def is_subscribed(self, job_id: str) -> bool:
        return self.subscribe_all or job_id in self.subscribed_jobs


class WebSocketManager:
    """Production-grade WebSocket connection manager (gevent-native)."""

    MAX_CONNECTIONS = 1000
    PING_INTERVAL = 30.0
    PONG_TIMEOUT = 10.0
    MAX_MISSED_PONGS = 3
    QUEUE_FULL_STRATEGY = "drop_oldest"

    def __init__(self):
        self._connections: Dict[str, WebSocketConnection] = {}
        self._lock = gevent.lock.BoundedSemaphore(1)
        self._shutdown_event = gevent.event.Event()
        self._heartbeat_greenlet: Optional[gevent.Greenlet] = None

    @property
    def connection_count(self) -> int:
        return len(self._connections)

    def start(self) -> None:
        self._shutdown_event.clear()
        self._heartbeat_greenlet = gevent.spawn(self._heartbeat_loop)
        logger.info("WebSocket manager started")

    def stop(self) -> None:
        logger.info("WebSocket manager stopping...")
        self._shutdown_event.set()

        if self._heartbeat_greenlet:
            self._heartbeat_greenlet.kill(block=False)

        with self._lock:
            for conn in list(self._connections.values()):
                self._close_connection(conn, "server_shutdown")
            self._connections.clear()

        logger.info("WebSocket manager stopped")

    def connect(self, ws) -> Optional[WebSocketConnection]:
        with self._lock:
            if len(self._connections) >= self.MAX_CONNECTIONS:
                logger.warning(f"Connection limit reached ({self.MAX_CONNECTIONS})")
                return None

            conn_id = str(uuid.uuid4())[:8]
            conn = WebSocketConnection(id=conn_id, ws=ws)
            conn.state = ConnectionState.CONNECTED
            self._connections[conn_id] = conn

            conn.send_greenlet = gevent.spawn(self._send_loop, conn)

            logger.info(f"[WS:{conn_id}] Connected. Total: {len(self._connections)}")
            return conn

    def disconnect(self, conn: WebSocketConnection) -> None:
        with self._lock:
            if conn.id in self._connections:
                self._close_connection(conn, "client_disconnect")
                del self._connections[conn.id]
                logger.info(f"[WS:{conn.id}] Disconnected. Total: {len(self._connections)}")

    def _close_connection(self, conn: WebSocketConnection, reason: str = "") -> None:
        if conn.state == ConnectionState.CLOSED:
            return

        conn.state = ConnectionState.CLOSING

        if conn.send_greenlet:
            conn.send_greenlet.kill(block=False)

        try:
            conn.ws.close()
        except Exception:
            pass

        conn.state = ConnectionState.CLOSED

    def _send_loop(self, conn: WebSocketConnection) -> None:
        try:
            while conn.state == ConnectionState.CONNECTED:
                try:
                    message = conn.message_queue.get(timeout=1.0)
                    try:
                        conn.ws.send(json.dumps(message))
                    except Exception as e:
                        logger.debug(f"[WS:{conn.id}] Send error: {e}")
                        break
                except gevent.queue.Empty:
                    continue
        except gevent.GreenletExit:
            pass

    def queue_message(self, conn: WebSocketConnection, message: dict) -> bool:
        if conn.state != ConnectionState.CONNECTED:
            return False

        try:
            if conn.message_queue.full():
                if self.QUEUE_FULL_STRATEGY == "drop_oldest":
                    try:
                        conn.message_queue.get_nowait()
                    except gevent.queue.Empty:
                        pass
                elif self.QUEUE_FULL_STRATEGY == "drop_newest":
                    return False

            conn.message_queue.put_nowait(message)
            return True
        except gevent.queue.Full:
            return False

    def broadcast(self, message: dict, job_id: Optional[str] = None) -> int:
        sent_count = 0

        with self._lock:
            connections = list(self._connections.values())

        for conn in connections:
            if conn.state != ConnectionState.CONNECTED:
                continue
            if job_id and not conn.is_subscribed(job_id):
                continue
            if self.queue_message(conn, message):
                sent_count += 1

        return sent_count

    def handle_message(self, conn: WebSocketConnection, data: str) -> None:
        try:
            message = json.loads(data)
            msg_type = message.get("type", "")

            if msg_type == "ping":
                self.queue_message(conn, {"type": "pong", "ts": time.time()})

            elif msg_type == "pong":
                conn.last_pong = time.time()
                conn.missed_pongs = 0

            elif msg_type == "subscribe":
                job_ids = message.get("job_ids", [])
                job_tokens = message.get("job_tokens", {})
                if isinstance(job_ids, list):
                    authorized = []
                    for job_id in job_ids:
                        token = None
                        if isinstance(job_tokens, dict):
                            token = job_tokens.get(job_id)
                        if not token and len(job_ids) == 1:
                            token = message.get("control_token")
                        try:
                            get_capability_service().validate(
                                token or "",
                                required_scope=CapabilityService.CONTROL_SCOPE,
                                job_id=job_id,
                            )
                            authorized.append(job_id)
                        except CapabilityError:
                            continue

                    conn.subscribed_jobs.update(authorized)
                    conn.authorized_jobs.update(authorized)
                    conn.subscribe_all = False
                    if authorized:
                        logger.debug(f"[WS:{conn.id}] Subscribed to jobs: {authorized}")
                    else:
                        self.queue_message(
                            conn,
                            {"type": "error", "data": {"error": "No valid job capabilities provided"}},
                        )

            elif msg_type == "unsubscribe":
                job_ids = message.get("job_ids", [])
                if isinstance(job_ids, list):
                    conn.subscribed_jobs.difference_update(job_ids)

            elif msg_type == "subscribe_all":
                self.queue_message(
                    conn,
                    {"type": "error", "data": {"error": "subscribe_all is not allowed without explicit job capabilities"}},
                )

        except json.JSONDecodeError:
            pass
        except Exception as e:
            logger.debug(f"[WS:{conn.id}] Message handling error: {e}")

    def _heartbeat_loop(self) -> None:
        while not self._shutdown_event.is_set():
            try:
                gevent.sleep(self.PING_INTERVAL)

                now = time.time()
                dead_connections = []

                with self._lock:
                    for conn in list(self._connections.values()):
                        if conn.state != ConnectionState.CONNECTED:
                            continue

                        if now - conn.last_pong > self.PONG_TIMEOUT:
                            conn.missed_pongs += 1
                            if conn.missed_pongs >= self.MAX_MISSED_PONGS:
                                logger.debug(f"[WS:{conn.id}] Dead (missed {conn.missed_pongs} pongs)")
                                dead_connections.append(conn)
                                continue

                        conn.last_ping = now
                        self.queue_message(conn, {"type": "ping", "ts": now})

                for conn in dead_connections:
                    self.disconnect(conn)

            except gevent.GreenletExit:
                break
            except Exception as e:
                logger.error(f"Heartbeat error: {e}")

    def get_stats(self) -> Dict[str, Any]:
        now = time.time()
        return {
            "total_connections": len(self._connections),
            "max_connections": self.MAX_CONNECTIONS,
            "connections": [
                {
                    "id": c.id,
                    "age_seconds": now - c.created_at,
                    "subscribed_jobs": len(c.subscribed_jobs),
                    "subscribe_all": c.subscribe_all,
                    "queue_size": c.message_queue.qsize(),
                }
                for c in self._connections.values()
            ],
        }


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

_manager: Optional[WebSocketManager] = None


def get_websocket_manager() -> WebSocketManager:
    global _manager
    if _manager is None:
        _manager = WebSocketManager()
    return _manager


# ---------------------------------------------------------------------------
# Broadcast helpers (called from JobService callbacks)
# ---------------------------------------------------------------------------

def broadcast_progress(job_id: str, progress: TranscodeProgress) -> None:
    manager = get_websocket_manager()
    message = {
        "type": "progress",
        "job_id": job_id,
        "data": {
            "progress": progress.percent,
            "frame": progress.frame,
            "fps": progress.fps,
            "time": progress.time,
            "speed": progress.speed,
        },
    }
    manager.broadcast(message, job_id=job_id)


def broadcast_status(job_id: str, status: JobStatus) -> None:
    manager = get_websocket_manager()
    message = {
        "type": "status_change",
        "job_id": job_id,
        "data": {"status": status.value},
    }
    manager.broadcast(message, job_id=job_id)

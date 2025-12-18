"""
WebSocket handling for GhostStream

Production-grade WebSocket manager with:
- Thread-safe connection management
- Per-connection message queues with backpressure
- Job subscription filtering
- Heartbeat/keepalive
- Graceful shutdown
- Connection limits
"""

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, Set, Optional, Any
from enum import Enum
from weakref import WeakSet

from fastapi import WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from ..models import JobStatus
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
    websocket: WebSocket
    state: ConnectionState = ConnectionState.CONNECTING
    subscribed_jobs: Set[str] = field(default_factory=set)
    subscribe_all: bool = True
    created_at: float = field(default_factory=time.time)
    last_ping: float = field(default_factory=time.time)
    last_pong: float = field(default_factory=time.time)
    message_queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=100))
    send_task: Optional[asyncio.Task] = None
    missed_pongs: int = 0
    
    def is_subscribed(self, job_id: str) -> bool:
        """Check if connection is subscribed to a job."""
        return self.subscribe_all or job_id in self.subscribed_jobs


class WebSocketManager:
    """
    Production-grade WebSocket connection manager.
    
    Features:
    - Async-safe connection tracking with locks
    - Per-connection send queues to prevent blocking
    - Backpressure handling (drops messages if queue full)
    - Job-based subscription filtering
    - Automatic ping/pong heartbeat
    - Connection limits
    - Graceful shutdown
    """
    
    MAX_CONNECTIONS = 1000
    PING_INTERVAL = 30.0
    PONG_TIMEOUT = 10.0
    MAX_MISSED_PONGS = 3
    QUEUE_FULL_STRATEGY = "drop_oldest"  # or "drop_newest", "block"
    
    def __init__(self):
        self._connections: Dict[str, WebSocketConnection] = {}
        self._lock = asyncio.Lock()
        self._shutdown_event = asyncio.Event()
        self._heartbeat_task: Optional[asyncio.Task] = None
        
    @property
    def connection_count(self) -> int:
        return len(self._connections)
    
    async def start(self) -> None:
        """Start the WebSocket manager (heartbeat task)."""
        self._shutdown_event.clear()
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        logger.info("WebSocket manager started")
    
    async def stop(self) -> None:
        """Gracefully stop the manager and close all connections."""
        logger.info("WebSocket manager stopping...")
        self._shutdown_event.set()
        
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        
        # Close all connections gracefully
        async with self._lock:
            close_tasks = []
            for conn in list(self._connections.values()):
                close_tasks.append(self._close_connection(conn, "server_shutdown"))
            
            if close_tasks:
                await asyncio.gather(*close_tasks, return_exceptions=True)
            
            self._connections.clear()
        
        logger.info("WebSocket manager stopped")
    
    async def connect(self, websocket: WebSocket) -> Optional[WebSocketConnection]:
        """Accept a new WebSocket connection."""
        async with self._lock:
            if len(self._connections) >= self.MAX_CONNECTIONS:
                logger.warning(f"Connection limit reached ({self.MAX_CONNECTIONS})")
                await websocket.close(code=1013, reason="Server overloaded")
                return None
            
            conn_id = str(uuid.uuid4())[:8]
            conn = WebSocketConnection(id=conn_id, websocket=websocket)
            
            try:
                await websocket.accept()
                conn.state = ConnectionState.CONNECTED
                self._connections[conn_id] = conn
                
                # Start the send queue processor for this connection
                conn.send_task = asyncio.create_task(self._send_loop(conn))
                
                logger.info(f"[WS:{conn_id}] Connected. Total: {len(self._connections)}")
                
                # Broadcast client connected event
                await self._broadcast_client_event("client_connected", len(self._connections))
                
                return conn
                
            except Exception as e:
                logger.error(f"[WS:{conn_id}] Failed to accept: {e}")
                return None
    
    async def disconnect(self, conn: WebSocketConnection) -> None:
        """Disconnect and cleanup a connection."""
        async with self._lock:
            if conn.id in self._connections:
                await self._close_connection(conn, "client_disconnect")
                del self._connections[conn.id]
                logger.info(f"[WS:{conn.id}] Disconnected. Total: {len(self._connections)}")
                
                # Broadcast client disconnected event
                await self._broadcast_client_event("client_disconnected", len(self._connections))
    
    async def _close_connection(self, conn: WebSocketConnection, reason: str = "") -> None:
        """Internal: close a connection and cleanup resources."""
        if conn.state == ConnectionState.CLOSED:
            return
        
        conn.state = ConnectionState.CLOSING
        
        # Cancel send task
        if conn.send_task:
            conn.send_task.cancel()
            try:
                await asyncio.wait_for(conn.send_task, timeout=1.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        
        # Close WebSocket
        try:
            if conn.websocket.client_state == WebSocketState.CONNECTED:
                await asyncio.wait_for(
                    conn.websocket.close(code=1000, reason=reason),
                    timeout=2.0
                )
        except Exception:
            pass
        
        conn.state = ConnectionState.CLOSED
    
    async def _send_loop(self, conn: WebSocketConnection) -> None:
        """Process send queue for a connection."""
        try:
            while conn.state == ConnectionState.CONNECTED:
                try:
                    message = await asyncio.wait_for(
                        conn.message_queue.get(),
                        timeout=1.0
                    )
                    
                    if conn.websocket.client_state == WebSocketState.CONNECTED:
                        await conn.websocket.send_json(message)
                    
                except asyncio.TimeoutError:
                    continue
                except Exception as e:
                    logger.debug(f"[WS:{conn.id}] Send error: {e}")
                    break
                    
        except asyncio.CancelledError:
            pass
    
    def queue_message(self, conn: WebSocketConnection, message: dict) -> bool:
        """
        Queue a message for sending. Returns False if dropped.
        """
        if conn.state != ConnectionState.CONNECTED:
            return False
        
        try:
            if conn.message_queue.full():
                if self.QUEUE_FULL_STRATEGY == "drop_oldest":
                    try:
                        conn.message_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                elif self.QUEUE_FULL_STRATEGY == "drop_newest":
                    return False
                # "block" would use put() instead of put_nowait()
            
            conn.message_queue.put_nowait(message)
            return True
            
        except asyncio.QueueFull:
            return False
    
    async def broadcast(self, message: dict, job_id: Optional[str] = None) -> int:
        """
        Broadcast message to all subscribed connections.
        Returns number of connections that received the message.
        """
        sent_count = 0
        
        async with self._lock:
            connections = list(self._connections.values())
        
        for conn in connections:
            if conn.state != ConnectionState.CONNECTED:
                continue
            
            # Check subscription filter
            if job_id and not conn.is_subscribed(job_id):
                continue
            
            if self.queue_message(conn, message):
                sent_count += 1
        
        return sent_count
    
    async def _broadcast_client_event(self, event_type: str, client_count: int) -> None:
        """Broadcast client connect/disconnect events to all connections."""
        message = {
            "type": event_type,
            "data": {
                "client_count": client_count,
                "timestamp": time.time()
            }
        }
        await self.broadcast(message)
    
    async def handle_message(self, conn: WebSocketConnection, data: str) -> None:
        """Handle incoming message from client."""
        try:
            message = json.loads(data)
            msg_type = message.get("type", "")
            
            if msg_type == "ping":
                self.queue_message(conn, {"type": "pong", "ts": time.time()})
                
            elif msg_type == "pong":
                conn.last_pong = time.time()
                conn.missed_pongs = 0
                
            elif msg_type == "subscribe":
                # Subscribe to specific job(s)
                job_ids = message.get("job_ids", [])
                if isinstance(job_ids, list):
                    conn.subscribed_jobs.update(job_ids)
                    conn.subscribe_all = False
                    logger.debug(f"[WS:{conn.id}] Subscribed to jobs: {job_ids}")
                    
            elif msg_type == "unsubscribe":
                job_ids = message.get("job_ids", [])
                if isinstance(job_ids, list):
                    conn.subscribed_jobs.difference_update(job_ids)
                    
            elif msg_type == "subscribe_all":
                conn.subscribe_all = True
                conn.subscribed_jobs.clear()
                
        except json.JSONDecodeError:
            pass
        except Exception as e:
            logger.debug(f"[WS:{conn.id}] Message handling error: {e}")
    
    async def _heartbeat_loop(self) -> None:
        """Send periodic pings and check for dead connections."""
        while not self._shutdown_event.is_set():
            try:
                await asyncio.sleep(self.PING_INTERVAL)
                
                now = time.time()
                dead_connections = []
                
                async with self._lock:
                    for conn in list(self._connections.values()):
                        if conn.state != ConnectionState.CONNECTED:
                            continue
                        
                        # Check for missed pongs
                        if now - conn.last_pong > self.PONG_TIMEOUT:
                            conn.missed_pongs += 1
                            
                            if conn.missed_pongs >= self.MAX_MISSED_PONGS:
                                logger.debug(f"[WS:{conn.id}] Dead (missed {conn.missed_pongs} pongs)")
                                dead_connections.append(conn)
                                continue
                        
                        # Send ping
                        conn.last_ping = now
                        self.queue_message(conn, {"type": "ping", "ts": now})
                
                # Clean up dead connections outside lock
                for conn in dead_connections:
                    await self.disconnect(conn)
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Heartbeat error: {e}")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get connection statistics."""
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
            ]
        }


# Global manager instance
_manager: Optional[WebSocketManager] = None


def get_websocket_manager() -> WebSocketManager:
    """Get or create the global WebSocket manager."""
    global _manager
    if _manager is None:
        _manager = WebSocketManager()
    return _manager


def broadcast_progress(job_id: str, progress: TranscodeProgress) -> None:
    """Broadcast progress update to all subscribed WebSocket clients."""
    manager = get_websocket_manager()
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
    asyncio.create_task(manager.broadcast(message, job_id=job_id))


def broadcast_status(job_id: str, status: JobStatus) -> None:
    """Broadcast status change to all subscribed WebSocket clients."""
    manager = get_websocket_manager()
    message = {
        "type": "status_change",
        "job_id": job_id,
        "data": {
            "status": status.value
        }
    }
    asyncio.create_task(manager.broadcast(message, job_id=job_id))


async def websocket_progress_handler(websocket: WebSocket) -> None:
    """WebSocket endpoint handler for real-time progress updates."""
    manager = get_websocket_manager()
    conn = await manager.connect(websocket)
    
    if not conn:
        return
    
    try:
        while conn.state == ConnectionState.CONNECTED:
            try:
                data = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=60.0
                )
                await manager.handle_message(conn, data)
                
            except asyncio.TimeoutError:
                continue
            except WebSocketDisconnect:
                break
            except Exception as e:
                logger.debug(f"[WS:{conn.id}] Receive error: {e}")
                break
                
    finally:
        await manager.disconnect(conn)


# Backwards compatibility
websocket_connections = []  # Deprecated, use get_websocket_manager().get_stats()

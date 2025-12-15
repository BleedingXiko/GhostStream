"""
WebSocket handling for GhostStream
"""

import asyncio
import json
import logging
from typing import List

from fastapi import WebSocket, WebSocketDisconnect

from ..models import JobStatus
from ..transcoding import TranscodeProgress

logger = logging.getLogger(__name__)

# Global WebSocket connections
websocket_connections: List[WebSocket] = []


def broadcast_progress(job_id: str, progress: TranscodeProgress) -> None:
    """Broadcast progress update to all WebSocket clients."""
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
    asyncio.create_task(_broadcast_message(message))


def broadcast_status(job_id: str, status: JobStatus) -> None:
    """Broadcast status change to all WebSocket clients."""
    message = {
        "type": "status_change",
        "job_id": job_id,
        "data": {
            "status": status.value
        }
    }
    asyncio.create_task(_broadcast_message(message))


async def _broadcast_message(message: dict) -> None:
    """Send message to all connected WebSocket clients."""
    disconnected = []
    for ws in websocket_connections[:]:  # Iterate over a copy
        try:
            await ws.send_json(message)
        except Exception:
            disconnected.append(ws)
    
    for ws in disconnected:
        if ws in websocket_connections:
            websocket_connections.remove(ws)


async def websocket_progress_handler(websocket: WebSocket) -> None:
    """WebSocket endpoint handler for real-time progress updates."""
    await websocket.accept()
    websocket_connections.append(websocket)
    
    logger.info(f"WebSocket client connected. Total connections: {len(websocket_connections)}")
    
    try:
        while True:
            # Keep connection alive and handle incoming messages
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                
                # Handle subscription messages
                try:
                    message = json.loads(data)
                    if message.get("type") == "ping":
                        await websocket.send_json({"type": "pong"})
                except json.JSONDecodeError:
                    pass
                    
            except asyncio.TimeoutError:
                # Send ping to keep connection alive
                try:
                    await websocket.send_json({"type": "ping"})
                except:
                    break
                    
    except WebSocketDisconnect:
        pass
    finally:
        if websocket in websocket_connections:
            websocket_connections.remove(websocket)
        logger.info(f"WebSocket client disconnected. Total connections: {len(websocket_connections)}")

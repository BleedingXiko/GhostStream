"""Canonical external websocket contract models and message types."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


MESSAGE_TYPE_IDENTIFY = "identify"
MESSAGE_TYPE_PING = "ping"
MESSAGE_TYPE_PONG = "pong"
MESSAGE_TYPE_SUBSCRIBE = "subscribe"
MESSAGE_TYPE_UNSUBSCRIBE = "unsubscribe"
MESSAGE_TYPE_SUBSCRIBE_ALL = "subscribe_all"
MESSAGE_TYPE_PROGRESS = "progress"
MESSAGE_TYPE_STATUS_CHANGE = "status_change"
MESSAGE_TYPE_ERROR = "error"


class WebSocketEnvelope(BaseModel):
    type: str


class WebSocketIdentifyMessage(WebSocketEnvelope):
    type: str = MESSAGE_TYPE_IDENTIFY
    client: Optional[str] = None


class WebSocketPingMessage(WebSocketEnvelope):
    type: str = MESSAGE_TYPE_PING
    ts: Optional[float] = None


class WebSocketPongMessage(WebSocketEnvelope):
    type: str = MESSAGE_TYPE_PONG
    ts: Optional[float] = None


class WebSocketSubscribeMessage(WebSocketEnvelope):
    type: str = MESSAGE_TYPE_SUBSCRIBE
    job_ids: List[str] = Field(default_factory=list)
    job_tokens: Optional[Dict[str, str]] = None
    control_token: Optional[str] = None


class WebSocketUnsubscribeMessage(WebSocketEnvelope):
    type: str = MESSAGE_TYPE_UNSUBSCRIBE
    job_ids: List[str] = Field(default_factory=list)


class WebSocketMessage(BaseModel):
    type: str
    job_id: str
    data: Dict[str, Any]


class WebSocketErrorMessage(WebSocketMessage):
    type: str = MESSAGE_TYPE_ERROR

from flask import Flask

import ghoststream.models as public_models
from ghoststream import __all__ as root_public_exports
from ghoststream.contracts.api import TranscodeRequest
from ghoststream.contracts.security import (
    API_KEY_HEADER,
    API_KEY_QUERY_PARAM,
    CONTROL_HEADER,
    STREAM_HEADER,
    STREAM_QUERY_PARAM,
)
from ghoststream.server.domain.security.capabilities import CapabilityService
from ghoststream.contracts.websocket import (
    MESSAGE_TYPE_ERROR,
    MESSAGE_TYPE_PING,
    MESSAGE_TYPE_PONG,
    MESSAGE_TYPE_PROGRESS,
    MESSAGE_TYPE_STATUS_CHANGE,
    MESSAGE_TYPE_SUBSCRIBE,
    MESSAGE_TYPE_SUBSCRIBE_ALL,
    MESSAGE_TYPE_UNSUBSCRIBE,
    WebSocketMessage,
)
from ghoststream.server.controllers.api import APIController
from ghoststream.server.controllers.ops import OperationsController
from ghoststream.server.controllers.security import append_token_to_playlist
from ghoststream.server.controllers.streams import StreamsController
from ghoststream.server.controllers.websocket import configure_http_app


def _normalized_route_map(app: Flask) -> set[tuple[str, tuple[str, ...]]]:
    routes = set()
    for rule in app.url_map.iter_rules():
        if rule.endpoint == "static":
            continue
        methods = tuple(sorted(rule.methods - {"HEAD", "OPTIONS"}))
        routes.add((rule.rule, methods))
    return routes


def test_http_routes_and_aliases_are_frozen() -> None:
    app = Flask("contract-freeze")
    configure_http_app(
        app,
        (
            OperationsController(),
            APIController(),
            StreamsController(),
        ),
    )

    assert _normalized_route_map(app) == {
        ("/api/health", ("GET",)),
        ("/api/health/detailed", ("GET",)),
        ("/api/ready", ("GET",)),
        ("/api/live", ("GET",)),
        ("/api/capabilities", ("GET",)),
        ("/api/stats", ("GET",)),
        ("/api/transcode/start", ("POST",)),
        ("/api/transcode/<job_id>/status", ("GET",)),
        ("/api/transcode/<job_id>/cancel", ("POST",)),
        ("/api/transcode/<job_id>/stream", ("GET",)),
        ("/api/transcode/<job_id>", ("DELETE",)),
        ("/api/transcode/<job_id>/leave", ("POST",)),
        ("/api/cleanup/stats", ("GET",)),
        ("/api/cleanup/run", ("POST",)),
        ("/api/streams/shared", ("GET",)),
        ("/api/clients", ("GET",)),
        ("/stream/<job_id>/<path:filename>", ("GET",)),
        ("/download/<job_id>", ("GET",)),
        ("/health", ("GET",)),
        ("/capabilities", ("GET",)),
        ("/transcode", ("POST",)),
        ("/transcode/<job_id>", ("GET",)),
        ("/transcode/<job_id>", ("DELETE",)),
    }


def test_public_root_exports_are_frozen() -> None:
    assert root_public_exports == [
        "GhostStreamClient",
        "GhostStreamServer",
        "GhostStreamLoadBalancer",
        "GhostStreamDiscoveryListener",
        "TranscodeJob",
        "TranscodeStatus",
        "ClientConfig",
        "LoadBalanceStrategy",
        "ServerStats",
        "__version__",
        "__author__",
    ]


def test_public_models_facade_points_to_contracts() -> None:
    assert public_models.TranscodeRequest is TranscodeRequest
    assert isinstance(WebSocketMessage(type="progress", job_id="job-1", data={}).model_dump(), dict)


def test_auth_contract_values_are_frozen() -> None:
    assert API_KEY_HEADER == "X-API-Key"
    assert API_KEY_QUERY_PARAM == "api_key"
    assert CONTROL_HEADER == "X-GhostStream-Control-Token"
    assert STREAM_HEADER == "X-GhostStream-Stream-Token"
    assert STREAM_QUERY_PARAM == "gst"


def test_websocket_message_types_are_frozen() -> None:
    assert {
        MESSAGE_TYPE_PING,
        MESSAGE_TYPE_PONG,
        MESSAGE_TYPE_SUBSCRIBE,
        MESSAGE_TYPE_UNSUBSCRIBE,
        MESSAGE_TYPE_SUBSCRIBE_ALL,
        MESSAGE_TYPE_PROGRESS,
        MESSAGE_TYPE_STATUS_CHANGE,
        MESSAGE_TYPE_ERROR,
    } == {
        "ping",
        "pong",
        "subscribe",
        "unsubscribe",
        "subscribe_all",
        "progress",
        "status_change",
        "error",
    }


def test_hls_token_propagation_is_frozen() -> None:
    content = "#EXTM3U\nsegment0.ts\nvariant.m3u8?foo=1\nhttps://example.com/skip.ts\n"
    rewritten = append_token_to_playlist(content, "token-123")

    assert "segment0.ts?gst=token-123" in rewritten
    assert "variant.m3u8?foo=1&gst=token-123" in rewritten
    assert "https://example.com/skip.ts" in rewritten


def test_stream_url_token_shape_is_frozen() -> None:
    capability_service = CapabilityService(b"test-secret", node_id="node-1")

    assert (
        capability_service.build_stream_url(
            "http://localhost:8765/stream/job-1/master.m3u8",
            "token-123",
        )
        == "http://localhost:8765/stream/job-1/master.m3u8?gst=token-123"
    )
    assert (
        capability_service.build_stream_url(
            "http://localhost:8765/stream/job-1/master.m3u8?foo=1",
            "token-123",
        )
        == "http://localhost:8765/stream/job-1/master.m3u8?foo=1&gst=token-123"
    )

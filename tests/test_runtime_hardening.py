import json
import tempfile
from contextlib import contextmanager
from pathlib import Path

from flask import Flask

from ghoststream.app.registry_keys import APP_CONFIG, CAPABILITY_SERVICE, CLIENT_PRESENCE, JOB_MANAGER
from ghoststream.__main__ import _resolve_tui_hosts
from ghoststream.config import GhostStreamConfig
from ghoststream.contracts.api import JobStatus, OutputConfig, OutputFormat, TranscodeMode, TranscodeRequest
from ghoststream.contracts.security import CONTROL_HEADER
from ghoststream.server.controllers.api import APIController
from ghoststream.server.controllers.streams import StreamsController
from ghoststream.server.controllers.websocket import (
    INTERNAL_DASHBOARD_HEADER,
    configure_http_app,
    extract_http_client_name,
)
from ghoststream.server.domain.jobs.models import Job
from ghoststream.server.domain.security.capabilities import CapabilityService
from ghoststream.server.services.client_presence import ClientPresenceService
from ghoststream.server.services.websocket import WebSocketManager
from ghoststream.specter.core.registry import registry


class _FakeWebSocket:
    def __init__(self) -> None:
        self.sent = []
        self.closed = False

    def send(self, message: str) -> None:
        self.sent.append(message)

    def close(self) -> None:
        self.closed = True


class _FakeJobManager:
    def __init__(self, capability_service: CapabilityService) -> None:
        self._capability_service = capability_service
        self.job = self._new_job(TranscodeRequest(source="http://example.com/video.mp4"))

    def _new_job(self, request: TranscodeRequest) -> Job:
        job = Job(id="job-123", request=request, status=JobStatus.PROCESSING)
        job.stream_url = f"http://127.0.0.1:8765/stream/{job.id}/master.m3u8"
        return job

    def _control_token(self, job_id: str) -> str:
        return self._capability_service.mint(
            job_id=job_id,
            scope=CapabilityService.CONTROL_SCOPE,
        )

    def _stream_url(self, job: Job) -> str:
        token = self._capability_service.mint(
            job_id=job.id,
            scope=CapabilityService.STREAM_SCOPE,
        )
        return self._capability_service.build_stream_url(job.stream_url or "", token)

    def create_job(self, request: TranscodeRequest, session_id=None, client_name=None) -> Job:
        self.job = self._new_job(request)
        return self.job

    def build_transcode_response(self, job: Job):
        return job.to_response(
            stream_url_override=self._stream_url(job),
            control_token=self._control_token(job.id),
        )

    def note_job_client(self, job_id: str, client_name) -> None:
        return None

    def get_job(self, job_id: str, touch: bool = True) -> Job | None:
        if self.job.id == job_id:
            return self.job
        return None

    def build_status_response(self, job: Job):
        return job.to_status_response(
            stream_url_override=self._stream_url(job),
            control_token=self._control_token(job.id),
        )


class _FakeDownloadJobManager:
    def __init__(self, output_path: str) -> None:
        request = TranscodeRequest(
            source="http://example.com/video.mp4",
            mode=TranscodeMode.BATCH,
            output=OutputConfig(format=OutputFormat.MP4),
        )
        self.job = Job(id="job-123", request=request, status=JobStatus.READY)
        self.job.output_path = output_path

    def note_job_client(self, job_id: str, client_name) -> None:
        return None

    def get_job(self, job_id: str, touch: bool = True) -> Job | None:
        if self.job.id == job_id:
            return self.job
        return None

    def touch_job(self, job_id: str) -> None:
        return None


@contextmanager
def _api_controller_client():
    registry.clear()
    config = GhostStreamConfig()
    capability_service = CapabilityService(b"test-secret", node_id="node-1")
    job_manager = _FakeJobManager(capability_service)

    registry.provide(APP_CONFIG, config, replace=True)
    registry.provide(CAPABILITY_SERVICE, capability_service, replace=True)
    registry.provide(CLIENT_PRESENCE, ClientPresenceService(), replace=True)
    registry.provide(JOB_MANAGER, job_manager, replace=True)

    app = Flask("api-controller-in-process")
    configure_http_app(app, (APIController(),))

    try:
        with app.test_client() as client:
            yield client
    finally:
        registry.clear()


@contextmanager
def _streams_controller_client(job_manager: _FakeDownloadJobManager):
    registry.clear()
    config = GhostStreamConfig()
    capability_service = CapabilityService(b"test-secret", node_id="node-1")

    registry.provide(APP_CONFIG, config, replace=True)
    registry.provide(CAPABILITY_SERVICE, capability_service, replace=True)
    registry.provide(CLIENT_PRESENCE, ClientPresenceService(), replace=True)
    registry.provide(JOB_MANAGER, job_manager, replace=True)

    # Use a non-cwd root path to reproduce Flask send_file behavior with
    # relative download paths.
    app = Flask("streams-controller-in-process", root_path=str(Path(__file__).resolve().parent))
    configure_http_app(app, (StreamsController(),))

    try:
        with app.test_client() as client:
            yield client, capability_service
    finally:
        registry.clear()


def test_dashboard_poller_does_not_register_as_http_client() -> None:
    app = Flask("client-name-regression")

    with app.test_request_context(
        "/api/health",
        headers={
            INTERNAL_DASHBOARD_HEADER: "tui-dashboard",
            "X-GhostStream-Client": "ShouldNotAppear",
        },
    ):
        assert extract_http_client_name() is None

    with app.test_request_context(
        "/api/health",
        headers={"X-GhostStream-Client": "RealClient"},
    ):
        assert extract_http_client_name() == "RealClient"


def test_websocket_receive_all_is_default_and_restorable() -> None:
    capability_service = CapabilityService(b"test-secret", node_id="node-1")
    manager = WebSocketManager(capability_service=capability_service)
    connection = manager.connect(_FakeWebSocket())

    assert connection is not None
    assert connection.subscribe_all is True

    token = capability_service.mint(
        job_id="job-123",
        scope=CapabilityService.CONTROL_SCOPE,
    )
    manager.handle_message(
        connection,
        json.dumps(
            {
                "type": "subscribe",
                "job_ids": ["job-123"],
                "job_tokens": {"job-123": token},
            }
        ),
    )

    assert connection.subscribe_all is False
    assert connection.subscribed_jobs == {"job-123"}

    manager.handle_message(connection, json.dumps({"type": "subscribe_all"}))

    assert connection.subscribe_all is True
    assert connection.subscribed_jobs == set()

    manager.disconnect(connection)


def test_tui_uses_local_poll_host_but_real_bind_host() -> None:
    assert _resolve_tui_hosts("0.0.0.0") == ("127.0.0.1", "0.0.0.0")
    assert _resolve_tui_hosts("10.0.0.108") == ("10.0.0.108", "10.0.0.108")


def test_api_controller_requires_control_token_without_binding_ports() -> None:
    with _api_controller_client() as client:
        started = client.post(
            "/api/transcode/start",
            json={"source": "http://example.com/video.mp4", "mode": "stream"},
        )
        assert started.status_code == 200

        job_id = started.get_json()["job_id"]
        status = client.get(f"/api/transcode/{job_id}/status")

        assert status.status_code == 403


def test_api_controller_returns_tokenized_urls_without_binding_ports() -> None:
    with _api_controller_client() as client:
        started = client.post(
            "/api/transcode/start",
            json={"source": "http://example.com/video.mp4", "mode": "stream"},
        )
        assert started.status_code == 200
        start_body = started.get_json()

        assert start_body["control_token"]
        assert "?gst=" in start_body["stream_url"]

        status = client.get(
            f"/api/transcode/{start_body['job_id']}/status",
            headers={CONTROL_HEADER: start_body["control_token"]},
        )
        assert status.status_code == 200

        status_body = status.get_json()
        assert status_body["control_token"]
        assert "?gst=" in status_body["stream_url"]


def test_download_endpoint_serves_relative_output_paths_without_binding_ports() -> None:
    with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
        output_path = (Path(temp_dir) / "output.mp4").resolve()
        output_bytes = b"x" * 2048
        output_path.write_bytes(output_bytes)
        relative_output_path = str(output_path.relative_to(Path.cwd()))

        with _streams_controller_client(_FakeDownloadJobManager(relative_output_path)) as (client, capability_service):
            token = capability_service.mint(
                job_id="job-123",
                scope=CapabilityService.STREAM_SCOPE,
            )
            response = client.get(f"/download/job-123?gst={token}")

        assert response.status_code == 200
        assert response.data == output_bytes
        assert response.headers["Content-Disposition"].startswith("attachment;")

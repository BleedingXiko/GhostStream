"""
GhostStream Test Configuration and Fixtures

Provides:
- Auto-generated test media files (no external downloads needed)
- Auto-cleanup of all test artifacts
- Shared fixtures for API client, engine, etc.
"""

import asyncio
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Generator, Optional
import aiohttp
import pytest
import httpx

# Add project root to path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from ghoststream.config import load_config, set_config
from ghoststream.app.entrypoints import create_runtime


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class WebSocketJSONClient:
    """Small JSON convenience wrapper around the sync websockets client."""

    def __init__(self, loop, connection):
        self._loop = loop
        self._connection = connection

    def send_json(self, payload) -> None:
        self._loop.run_until_complete(self._connection.send_json(payload))

    def receive_json(self):
        message = self._loop.run_until_complete(self._connection.receive_json())
        return message


class WebSocketConnectionContext:
    def __init__(self, url: str):
        self._url = url
        self._loop = None
        self._session = None
        self._connection = None

    def __enter__(self) -> WebSocketJSONClient:
        self._loop = asyncio.new_event_loop()
        self._session, self._connection = self._loop.run_until_complete(self._open())
        return WebSocketJSONClient(self._loop, self._connection)

    async def _open(self):
        session = aiohttp.ClientSession()
        connection = await session.ws_connect(self._url, autoping=True, compress=0)
        return session, connection

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._loop is not None and self._connection is not None:
            self._loop.run_until_complete(self._connection.close())
        if self._loop is not None and self._session is not None:
            self._loop.run_until_complete(self._session.__aexit__(exc_type, exc, tb))
            self._loop.close()
        return None


class GhostStreamTestClient:
    """HTTP + WebSocket test client for the live GhostStream runtime."""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self._http = httpx.Client(base_url=self.base_url, timeout=10.0)

    def get(self, *args, **kwargs):
        return self._http.get(*args, **kwargs)

    def post(self, *args, **kwargs):
        return self._http.post(*args, **kwargs)

    def delete(self, *args, **kwargs):
        return self._http.delete(*args, **kwargs)

    def websocket_connect(self, path: str) -> WebSocketConnectionContext:
        ws_base = self.base_url.replace("http://", "ws://", 1).replace("https://", "wss://", 1)
        return WebSocketConnectionContext(f"{ws_base}{path}")

    def close(self) -> None:
        self._http.close()


# =============================================================================
# TEST MEDIA GENERATION
# =============================================================================

class TestMediaGenerator:
    """
    Generates test media files using FFmpeg.
    No external downloads - creates synthetic test videos.
    """
    
    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._ffmpeg = shutil.which("ffmpeg")
    
    @property
    def has_ffmpeg(self) -> bool:
        return self._ffmpeg is not None
    
    def generate_test_video(
        self,
        name: str = "test_video",
        duration: int = 5,
        width: int = 1280,
        height: int = 720,
        fps: int = 30,
        codec: str = "libx264",
        audio: bool = True
    ) -> Optional[Path]:
        """
        Generate a test video with color bars and tone.
        
        Args:
            name: Output filename (without extension)
            duration: Duration in seconds
            width: Video width
            height: Video height
            fps: Frames per second
            codec: Video codec to use
            audio: Include audio track
        
        Returns:
            Path to generated video, or None if FFmpeg not available
        """
        if not self.has_ffmpeg:
            return None
        
        output_path = self.output_dir / f"{name}.mp4"
        
        # Build FFmpeg command for test pattern
        cmd = [
            self._ffmpeg,
            "-y",  # Overwrite
            "-f", "lavfi",
            "-i", f"testsrc=duration={duration}:size={width}x{height}:rate={fps}",
        ]
        
        if audio:
            # Add sine wave audio
            cmd.extend([
                "-f", "lavfi",
                "-i", f"sine=frequency=440:duration={duration}",
            ])
        
        cmd.extend([
            "-c:v", codec,
            "-preset", "ultrafast",  # Fast encoding for tests
            "-pix_fmt", "yuv420p",
        ])
        
        if audio:
            cmd.extend(["-c:a", "aac", "-b:a", "128k"])
        
        cmd.append(str(output_path))
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=60
            )
            if result.returncode == 0 and output_path.exists():
                return output_path
        except (subprocess.TimeoutExpired, Exception) as e:
            print(f"Failed to generate test video: {e}")
        
        return None
    
    def generate_test_videos(self) -> dict:
        """
        Generate a set of test videos for different scenarios.
        
        Returns:
            Dict mapping video type to path
        """
        videos = {}
        
        # Standard 720p test video (5 seconds)
        path = self.generate_test_video("test_720p", duration=5, width=1280, height=720)
        if path:
            videos["720p"] = path
        
        # Short 1080p video (3 seconds)
        path = self.generate_test_video("test_1080p", duration=3, width=1920, height=1080)
        if path:
            videos["1080p"] = path
        
        # Very short video for quick tests (1 second)
        path = self.generate_test_video("test_quick", duration=1, width=640, height=360)
        if path:
            videos["quick"] = path
        
        # Video without audio
        path = self.generate_test_video("test_no_audio", duration=2, width=640, height=360, audio=False)
        if path:
            videos["no_audio"] = path
        
        return videos


# =============================================================================
# PYTEST FIXTURES
# =============================================================================

@pytest.fixture(scope="session")
def test_media_dir(tmp_path_factory) -> Path:
    """
    Session-scoped temp directory for test media.
    Auto-cleaned after all tests complete.
    """
    return tmp_path_factory.mktemp("ghoststream_test_media")


@pytest.fixture(scope="session")
def media_generator(test_media_dir) -> TestMediaGenerator:
    """Session-scoped media generator."""
    return TestMediaGenerator(test_media_dir)


@pytest.fixture(scope="session")
def test_videos(media_generator) -> dict:
    """
    Generate test videos once per session.
    Returns dict of video paths by type.
    """
    if not media_generator.has_ffmpeg:
        pytest.skip("FFmpeg not available for test media generation")
    
    videos = media_generator.generate_test_videos()
    if not videos:
        pytest.skip("Failed to generate test videos")
    
    return videos


@pytest.fixture(scope="session")
def quick_test_video(test_videos) -> Path:
    """Quick 1-second test video for fast tests."""
    return test_videos.get("quick")


@pytest.fixture(scope="session")
def test_video_720p(test_videos) -> Path:
    """Standard 720p test video."""
    return test_videos.get("720p")


@pytest.fixture(scope="session")
def test_video_1080p(test_videos) -> Path:
    """1080p test video."""
    return test_videos.get("1080p")


@pytest.fixture(scope="module")
def test_config():
    """
    Load and set test configuration.
    Uses a temp directory for transcoding output.
    """
    config = load_config()

    config.server.host = "127.0.0.1"
    config.server.port = _find_free_port()
    config.mdns.enabled = False
    config.ghosthub.auto_register = False

    # Use temp directory for test transcoding
    config.transcoding.temp_directory = tempfile.mkdtemp(prefix="ghoststream_test_")
    config.transcoding.max_concurrent_jobs = 2
    config.transcoding.stall_timeout = 30  # Shorter for tests
    config.logging.level = "WARNING"  # Less noise in tests
    config.security.state_directory = tempfile.mkdtemp(prefix="ghoststream_state_")

    set_config(config)

    yield config

    # Cleanup temp directory
    if Path(config.transcoding.temp_directory).exists():
        shutil.rmtree(config.transcoding.temp_directory, ignore_errors=True)
    if Path(config.security.state_directory).exists():
        shutil.rmtree(config.security.state_directory, ignore_errors=True)


@pytest.fixture(scope="module")
def api_client(test_config) -> Generator[GhostStreamTestClient, None, None]:
    """
    Live HTTP/WebSocket client bound to the real GhostStream runtime.
    """
    runtime = create_runtime(test_config)
    stop_event = threading.Event()
    runtime_errors = []

    def _runtime_thread() -> None:
        import gevent

        try:
            runtime.start()
            while not stop_event.is_set():
                gevent.sleep(0.1)
        except Exception as exc:
            runtime_errors.append(exc)
        finally:
            try:
                runtime.stop()
            except Exception as exc:
                runtime_errors.append(exc)

    thread = threading.Thread(
        target=_runtime_thread,
        daemon=True,
        name="ghoststream-test-runtime",
    )
    thread.start()

    base_url = f"http://{test_config.server.host}:{test_config.server.port}"
    client = GhostStreamTestClient(base_url)

    deadline = time.time() + 5.0
    while True:
        if runtime_errors:
            client.close()
            raise RuntimeError("GhostStream test runtime failed to start") from runtime_errors[0]

        try:
            response = client.get("/api/health")
            if response.status_code == 200:
                break
        except Exception:
            pass

        if time.time() >= deadline:
            stop_event.set()
            thread.join(timeout=2.0)
            client.close()
            raise RuntimeError("GhostStream test runtime did not become ready in time")
        time.sleep(0.1)

    try:
        yield client
    finally:
        stop_event.set()
        thread.join(timeout=10.0)
        client.close()
        if thread.is_alive():
            raise RuntimeError("GhostStream test runtime did not shut down cleanly")


@pytest.fixture
def temp_output_dir(tmp_path) -> Path:
    """Per-test temp directory for output files."""
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    return output_dir


@pytest.fixture
def cleanup_jobs(api_client):
    """
    Fixture that tracks and cleans up jobs after test.
    Usage:
        def test_something(cleanup_jobs, api_client):
            job = api_client.post("/api/transcode/start", ...).json()
            cleanup_jobs.append(job["job_id"])
    """
    job_ids = []
    yield job_ids
    
    # Cleanup all tracked jobs
    for job_id in job_ids:
        try:
            if isinstance(job_id, tuple):
                actual_job_id, control_token = job_id
                api_client.delete(
                    f"/api/transcode/{actual_job_id}",
                    headers={"X-GhostStream-Control-Token": control_token},
                )
            else:
                api_client.delete(f"/api/transcode/{job_id}")
        except Exception:
            pass


# =============================================================================
# HTTP SERVER FOR TEST MEDIA
# =============================================================================

@pytest.fixture(scope="session")
def http_server(test_media_dir, test_videos):
    """
    Start a simple HTTP server to serve test media files.
    Required because GhostStream fetches media via HTTP.
    """
    from http.server import HTTPServer, SimpleHTTPRequestHandler
    
    class QuietHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(test_media_dir), **kwargs)
        
        def log_message(self, format, *args):
            pass  # Suppress logging
    
    # Find available port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        port = s.getsockname()[1]
    
    server = HTTPServer(('localhost', port), QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    
    yield f"http://localhost:{port}"
    
    server.shutdown()
    server.server_close()
    thread.join(timeout=5.0)


@pytest.fixture
def test_video_url(http_server, quick_test_video) -> str:
    """URL to quick test video via HTTP server."""
    if quick_test_video:
        return f"{http_server}/{quick_test_video.name}"
    return None


# =============================================================================
# ASYNC FIXTURES
# =============================================================================

@pytest.fixture
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# =============================================================================
# SKIP CONDITIONS
# =============================================================================

def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "slow: marks tests as slow (deselect with '-m \"not slow\"')"
    )
    config.addinivalue_line(
        "markers", "integration: marks tests as integration tests"
    )
    config.addinivalue_line(
        "markers", "requires_ffmpeg: marks tests that require FFmpeg"
    )


@pytest.fixture
def requires_ffmpeg():
    """Skip test if FFmpeg not available."""
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg not available")

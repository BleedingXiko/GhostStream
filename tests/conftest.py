"""
GhostStream Test Configuration and Fixtures

Provides:
- Auto-generated test media files (no external downloads needed)
- Auto-cleanup of all test artifacts
- Shared fixtures for API client, engine, etc.
"""

import asyncio
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Generator, Optional
import pytest
from fastapi.testclient import TestClient

# Add project root to path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from ghoststream.api import app
from ghoststream.config import load_config, set_config, get_config


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
    
    # Use temp directory for test transcoding
    config.transcoding.temp_directory = tempfile.mkdtemp(prefix="ghoststream_test_")
    config.transcoding.max_concurrent_jobs = 2
    config.transcoding.stall_timeout = 30  # Shorter for tests
    config.logging.level = "WARNING"  # Less noise in tests
    
    set_config(config)
    
    yield config
    
    # Cleanup temp directory
    if Path(config.transcoding.temp_directory).exists():
        shutil.rmtree(config.transcoding.temp_directory, ignore_errors=True)


@pytest.fixture(scope="module")
def api_client(test_config) -> Generator[TestClient, None, None]:
    """
    Test client for API endpoints.
    Auto-cleanup on teardown.
    """
    with TestClient(app) as client:
        yield client


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
    import threading
    from http.server import HTTPServer, SimpleHTTPRequestHandler
    
    class QuietHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(test_media_dir), **kwargs)
        
        def log_message(self, format, *args):
            pass  # Suppress logging
    
    # Find available port
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        port = s.getsockname()[1]
    
    server = HTTPServer(('localhost', port), QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    
    yield f"http://localhost:{port}"
    
    server.shutdown()


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

"""
Tests for GhostStream API endpoints
"""

import pytest

from ghoststream import __version__


class TestHealthEndpoint:
    """Tests for /api/health endpoint."""

    def test_health_returns_200(self, api_client):
        """Health check should return 200."""
        response = api_client.get("/api/health")
        assert response.status_code == 200

    def test_health_returns_version(self, api_client):
        """Health check should return version."""
        response = api_client.get("/api/health")
        data = response.json()
        assert data["version"] == __version__
        assert data["status"] == "healthy"

    def test_health_returns_job_counts(self, api_client):
        """Health check should return job counts."""
        response = api_client.get("/api/health")
        data = response.json()
        assert "current_jobs" in data
        assert "queued_jobs" in data
        assert "uptime_seconds" in data


class TestCapabilitiesEndpoint:
    """Tests for /api/capabilities endpoint."""

    def test_capabilities_returns_200(self, api_client):
        """Capabilities should return 200."""
        response = api_client.get("/api/capabilities")
        assert response.status_code == 200

    def test_capabilities_returns_hw_accels(self, api_client):
        """Capabilities should return hardware accelerators."""
        response = api_client.get("/api/capabilities")
        data = response.json()
        assert "hw_accels" in data
        assert isinstance(data["hw_accels"], list)

    def test_capabilities_returns_codecs(self, api_client):
        """Capabilities should return supported codecs."""
        response = api_client.get("/api/capabilities")
        data = response.json()
        assert "video_codecs" in data
        assert "audio_codecs" in data
        assert "formats" in data

    def test_capabilities_returns_platform(self, api_client):
        """Capabilities should return platform info."""
        response = api_client.get("/api/capabilities")
        data = response.json()
        assert "platform" in data
        assert "ffmpeg_version" in data


class TestStatsEndpoint:
    """Tests for /api/stats endpoint."""

    def test_stats_returns_200(self, api_client):
        """Stats should return 200."""
        response = api_client.get("/api/stats")
        assert response.status_code == 200

    def test_stats_returns_counters(self, api_client):
        """Stats should return job counters."""
        response = api_client.get("/api/stats")
        data = response.json()
        assert "total_jobs_processed" in data
        assert "successful_jobs" in data
        assert "failed_jobs" in data
        assert "cancelled_jobs" in data


class TestTranscodeEndpoints:
    """Tests for transcoding endpoints."""

    def test_start_transcode_requires_source(self, api_client):
        """Start transcode should require source."""
        response = api_client.post("/api/transcode/start", json={})
        assert response.status_code == 422  # Validation error

    def test_start_transcode_accepts_valid_request(self, api_client):
        """Start transcode should accept valid request."""
        response = api_client.post("/api/transcode/start", json={
            "source": "http://example.com/video.mp4",
            "mode": "stream",
            "output": {
                "format": "hls",
                "video_codec": "h264",
                "audio_codec": "aac",
                "resolution": "1080p"
            }
        })
        assert response.status_code == 200
        data = response.json()
        assert "job_id" in data
        assert "control_token" in data
        assert data["status"] in ["queued", "processing"]

    def test_get_nonexistent_job(self, api_client):
        """Getting a job without a control token should be rejected."""
        response = api_client.get("/api/transcode/nonexistent-id/status")
        assert response.status_code == 403

    def test_cancel_nonexistent_job(self, api_client):
        """Canceling a job without a control token should be rejected."""
        response = api_client.post("/api/transcode/nonexistent-id/cancel")
        assert response.status_code == 403


class TestWebSocket:
    """Tests for WebSocket endpoint."""

    def test_websocket_connects(self, api_client):
        """WebSocket should accept connections."""
        with api_client.websocket_connect("/ws/progress") as websocket:
            # Send ping
            websocket.send_json({"type": "ping"})
            # Should receive pong
            data = websocket.receive_json()
            assert data["type"] == "pong"

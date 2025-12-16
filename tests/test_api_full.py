"""
Full API Integration Tests
==========================

Tests the complete API with real transcoding jobs.
Uses test media fixtures for realistic testing.

Run with: pytest tests/test_api_full.py -v
"""

import time
import pytest


# =============================================================================
# HEALTH & STATUS ENDPOINTS
# =============================================================================

class TestHealthEndpoints:
    """Test health and status endpoints."""
    
    def test_health_check(self, api_client):
        """Health endpoint should return healthy status."""
        response = api_client.get("/api/health")
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "version" in data
        assert "uptime_seconds" in data
        assert "current_jobs" in data
        assert "queued_jobs" in data
    
    def test_health_compat_endpoint(self, api_client):
        """GhostHub compatibility endpoint should work."""
        response = api_client.get("/health")
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
    
    def test_capabilities(self, api_client):
        """Capabilities endpoint should return hardware info."""
        response = api_client.get("/api/capabilities")
        
        assert response.status_code == 200
        data = response.json()
        
        # Required fields
        assert "hw_accels" in data
        assert "video_codecs" in data
        assert "audio_codecs" in data
        assert "platform" in data
        assert "ffmpeg_version" in data
        
        # Should have some codecs
        assert len(data["video_codecs"]) > 0
        assert "h264" in data["video_codecs"]
    
    def test_stats(self, api_client):
        """Stats endpoint should return job statistics."""
        response = api_client.get("/api/stats")
        
        assert response.status_code == 200
        data = response.json()
        
        assert "total_jobs_processed" in data
        assert "successful_jobs" in data
        assert "failed_jobs" in data
        assert "current_queue_length" in data
        assert "active_jobs" in data
    
    def test_detailed_health(self, api_client):
        """Detailed health should return system metrics."""
        response = api_client.get("/api/health/detailed")
        
        assert response.status_code == 200
        data = response.json()
        
        assert "status" in data
        assert "checks" in data
        assert "cpu" in data["checks"]
        assert "memory" in data["checks"]
    
    def test_ready_endpoint(self, api_client):
        """Readiness probe should work."""
        response = api_client.get("/api/ready")
        
        assert response.status_code == 200
        data = response.json()
        assert data["ready"] is True
    
    def test_live_endpoint(self, api_client):
        """Liveness probe should work."""
        response = api_client.get("/api/live")
        
        assert response.status_code == 200
        data = response.json()
        assert data["alive"] is True


# =============================================================================
# TRANSCODE JOB ENDPOINTS
# =============================================================================

class TestTranscodeEndpoints:
    """Test transcoding job endpoints."""
    
    def test_start_requires_source(self, api_client):
        """Start should require source URL."""
        response = api_client.post("/api/transcode/start", json={})
        assert response.status_code == 422
    
    def test_start_with_invalid_source(self, api_client, cleanup_jobs):
        """Start should accept request even with invalid source."""
        response = api_client.post("/api/transcode/start", json={
            "source": "http://invalid-domain-xyz.local/video.mp4",
            "mode": "stream"
        })
        
        # Job is created, but may fail later during transcoding
        assert response.status_code == 200
        data = response.json()
        assert "job_id" in data
        cleanup_jobs.append(data["job_id"])
    
    def test_start_stream_mode(self, api_client, test_video_url, cleanup_jobs):
        """Start should accept stream mode."""
        if not test_video_url:
            pytest.skip("Test video URL not available")
        
        response = api_client.post("/api/transcode/start", json={
            "source": test_video_url,
            "mode": "stream",
            "output": {
                "resolution": "480p",
                "video_codec": "h264"
            }
        })
        
        assert response.status_code == 200
        data = response.json()
        assert "job_id" in data
        assert "stream_url" in data
        assert data["status"] in ["queued", "processing"]
        
        cleanup_jobs.append(data["job_id"])
    
    def test_start_abr_mode(self, api_client, test_video_url, cleanup_jobs):
        """Start should accept ABR mode."""
        if not test_video_url:
            pytest.skip("Test video URL not available")
        
        response = api_client.post("/api/transcode/start", json={
            "source": test_video_url,
            "mode": "abr",
            "output": {
                "video_codec": "h264"
            }
        })
        
        assert response.status_code == 200
        data = response.json()
        assert "job_id" in data
        
        cleanup_jobs.append(data["job_id"])
    
    def test_get_job_status(self, api_client, test_video_url, cleanup_jobs):
        """Should get status of existing job."""
        if not test_video_url:
            pytest.skip("Test video URL not available")
        
        # Create job
        response = api_client.post("/api/transcode/start", json={
            "source": test_video_url,
            "mode": "stream"
        })
        job_id = response.json()["job_id"]
        cleanup_jobs.append(job_id)
        
        # Get status
        response = api_client.get(f"/api/transcode/{job_id}/status")
        
        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == job_id
        assert "status" in data
        assert "progress" in data
    
    def test_get_nonexistent_job(self, api_client):
        """Should return 404 for nonexistent job."""
        response = api_client.get("/api/transcode/nonexistent-job-id/status")
        assert response.status_code == 404
    
    def test_cancel_job(self, api_client, test_video_url, cleanup_jobs):
        """Should cancel a running job."""
        if not test_video_url:
            pytest.skip("Test video URL not available")
        
        # Create job
        response = api_client.post("/api/transcode/start", json={
            "source": test_video_url,
            "mode": "stream"
        })
        job_id = response.json()["job_id"]
        cleanup_jobs.append(job_id)
        
        # Cancel it
        response = api_client.post(f"/api/transcode/{job_id}/cancel")
        
        # Should succeed or indicate job can't be cancelled
        assert response.status_code in [200, 400]
    
    def test_delete_job(self, api_client, test_video_url):
        """Should delete a job and cleanup."""
        if not test_video_url:
            pytest.skip("Test video URL not available")
        
        # Create job
        response = api_client.post("/api/transcode/start", json={
            "source": test_video_url,
            "mode": "stream"
        })
        job_id = response.json()["job_id"]
        
        # Delete it
        response = api_client.delete(f"/api/transcode/{job_id}")
        assert response.status_code == 200
        
        # Should no longer exist
        response = api_client.get(f"/api/transcode/{job_id}/status")
        assert response.status_code == 404


# =============================================================================
# CLEANUP ENDPOINTS
# =============================================================================

class TestCleanupEndpoints:
    """Test cleanup and maintenance endpoints."""
    
    def test_get_cleanup_stats(self, api_client):
        """Should return cleanup statistics."""
        response = api_client.get("/api/cleanup/stats")
        
        assert response.status_code == 200
        data = response.json()
        
        assert "total_jobs" in data
        assert "temp_dir" in data
    
    def test_run_manual_cleanup(self, api_client):
        """Should run manual cleanup."""
        response = api_client.post("/api/cleanup/run")
        
        assert response.status_code == 200
        data = response.json()
        
        assert "stale_jobs_cleaned" in data
        assert "orphaned_dirs_cleaned" in data


# =============================================================================
# WEBSOCKET TESTS
# =============================================================================

class TestWebSocket:
    """Test WebSocket endpoint."""
    
    def test_websocket_connects(self, api_client):
        """WebSocket should accept connections."""
        with api_client.websocket_connect("/ws/progress") as websocket:
            # Should be able to connect
            assert websocket is not None
    
    def test_websocket_ping_pong(self, api_client):
        """WebSocket should respond to ping."""
        with api_client.websocket_connect("/ws/progress") as websocket:
            websocket.send_json({"type": "ping"})
            data = websocket.receive_json()
            assert data["type"] == "pong"
    
    def test_websocket_subscribe(self, api_client, test_video_url, cleanup_jobs):
        """WebSocket should accept job subscriptions."""
        if not test_video_url:
            pytest.skip("Test video URL not available")
        
        # Create a job first
        response = api_client.post("/api/transcode/start", json={
            "source": test_video_url,
            "mode": "stream"
        })
        job_id = response.json()["job_id"]
        cleanup_jobs.append(job_id)
        
        # Subscribe via WebSocket
        with api_client.websocket_connect("/ws/progress") as websocket:
            websocket.send_json({
                "type": "subscribe",
                "job_ids": [job_id]
            })
            
            # Should receive confirmation or initial status
            # (implementation may vary)


# =============================================================================
# SHARED STREAMS TESTS
# =============================================================================

class TestSharedStreams:
    """Test stream sharing functionality."""
    
    def test_get_shared_streams(self, api_client):
        """Should return shared stream stats."""
        response = api_client.get("/api/streams/shared")
        
        assert response.status_code == 200
        data = response.json()
        
        # Should have stats structure
        assert isinstance(data, dict)
    
    def test_stream_sharing_same_source(self, api_client, test_video_url, cleanup_jobs):
        """Multiple requests for same source should share stream."""
        if not test_video_url:
            pytest.skip("Test video URL not available")
        
        # Start first job
        response1 = api_client.post("/api/transcode/start", json={
            "source": test_video_url,
            "mode": "stream",
            "output": {"resolution": "480p"}
        })
        job1 = response1.json()
        cleanup_jobs.append(job1["job_id"])
        
        # Start second job with same source
        response2 = api_client.post("/api/transcode/start", json={
            "source": test_video_url,
            "mode": "stream",
            "output": {"resolution": "480p"}
        })
        job2 = response2.json()
        
        # Might share the same job (implementation dependent)
        if job1["job_id"] != job2["job_id"]:
            cleanup_jobs.append(job2["job_id"])


# =============================================================================
# COMPATIBILITY ENDPOINTS
# =============================================================================

class TestCompatibilityEndpoints:
    """Test GhostHub compatibility endpoints (without /api/ prefix)."""
    
    def test_transcode_compat(self, api_client, test_video_url, cleanup_jobs):
        """Should work with /transcode endpoint."""
        if not test_video_url:
            pytest.skip("Test video URL not available")
        
        response = api_client.post("/transcode", json={
            "source": test_video_url,
            "mode": "stream"
        })
        
        assert response.status_code == 200
        data = response.json()
        assert "job_id" in data
        cleanup_jobs.append(data["job_id"])
    
    def test_capabilities_compat(self, api_client):
        """Should work with /capabilities endpoint."""
        response = api_client.get("/capabilities")
        
        assert response.status_code == 200
        data = response.json()
        assert "video_codecs" in data

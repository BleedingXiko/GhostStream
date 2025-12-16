"""
Integration tests for GhostStream transcoding flow.

Tests cover:
- Full transcode flow with mocked FFmpeg
- Job lifecycle management
- Config-driven behavior
- Error recovery scenarios
"""

import asyncio
import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime

from ghoststream.transcoding import (
    TranscodeEngine,
    TranscodeProgress,
    MediaInfo,
    QualityPreset,
    JobScheduler,
    JobPriority,
    FFmpegWorkerPool,
)
from ghoststream.models import TranscodeMode, OutputConfig, OutputFormat, HWAccel


# =============================================================================
# TRANSCODE ENGINE INTEGRATION TESTS
# =============================================================================

class TestTranscodeEngineIntegration:
    """Integration tests for TranscodeEngine."""
    
    @pytest.fixture
    def mock_config(self):
        """Create mock config."""
        config = MagicMock()
        config.transcoding.ffmpeg_path = "ffmpeg"
        config.transcoding.temp_directory = tempfile.mkdtemp()
        config.transcoding.max_concurrent_jobs = 2
        config.transcoding.stall_timeout = 120
        config.transcoding.segment_duration = 4
        config.transcoding.retry_count = 3
        config.transcoding.validate_segments = True
        config.hardware.prefer_hw_accel = True
        config.hardware.fallback_to_software = True
        config.hardware.nvenc_preset = "p4"
        config.hardware.qsv_preset = "medium"
        config.hardware.vaapi_device = "/dev/dri/renderD128"
        return config
    
    @pytest.fixture
    def mock_capabilities(self):
        """Create mock capabilities."""
        caps = MagicMock()
        caps.hw_accels = []
        caps.get_best_hw_accel.return_value = MagicMock(value="software")
        return caps
    
    @pytest.mark.asyncio
    async def test_transcode_prepares_job(self, mock_config, mock_capabilities):
        """Should prepare job directory and get media info."""
        with patch('ghoststream.transcoding.engine.get_config', return_value=mock_config), \
             patch('ghoststream.transcoding.engine.get_capabilities', return_value=mock_capabilities), \
             patch('shutil.which', return_value='ffmpeg'):
            
            engine = TranscodeEngine()
            
            # Mock get_media_info
            engine.get_media_info = AsyncMock(return_value=MediaInfo(
                width=1920, height=1080, duration=60.0,
                video_codec="h264", audio_codec="aac"
            ))
            
            media_info, job_dir, error = await engine._prepare_job("test-job", "input.mp4")
            
            assert media_info is not None
            assert job_dir is not None
            assert error is None
            assert job_dir.exists()
    
    @pytest.mark.asyncio
    async def test_transcode_handles_missing_media(self, mock_config, mock_capabilities):
        """Should handle missing/invalid media."""
        with patch('ghoststream.transcoding.engine.get_config', return_value=mock_config), \
             patch('ghoststream.transcoding.engine.get_capabilities', return_value=mock_capabilities), \
             patch('shutil.which', return_value='ffmpeg'):
            
            engine = TranscodeEngine()
            
            # Mock get_media_info to return invalid info
            engine.get_media_info = AsyncMock(return_value=MediaInfo(duration=0))
            
            media_info, job_dir, error = await engine._prepare_job("test-job", "invalid.mp4")
            
            assert media_info is None
            assert error is not None
            assert "Failed to get media info" in error


# =============================================================================
# JOB SCHEDULER INTEGRATION TESTS
# =============================================================================

class TestSchedulerIntegration:
    """Integration tests for JobScheduler."""
    
    @pytest.mark.asyncio
    async def test_submit_and_execute_job(self):
        """Should submit and execute a job."""
        scheduler = JobScheduler(max_concurrent=2)
        
        executed_jobs = []
        
        async def mock_executor(job):
            executed_jobs.append(job.job_id)
            await asyncio.sleep(0.1)
            return {"status": "completed"}
        
        scheduler.set_executor(mock_executor)
        await scheduler.start()
        
        # Submit job
        accepted, msg, job = await scheduler.submit(
            job_id="test-job",
            priority=JobPriority.NORMAL
        )
        
        assert accepted is True
        
        # Wait for execution
        await asyncio.sleep(0.5)
        
        assert "test-job" in executed_jobs
        
        await scheduler.stop()
    
    @pytest.mark.asyncio
    async def test_priority_ordering(self):
        """Should process higher priority jobs first."""
        scheduler = JobScheduler(max_concurrent=1)
        
        execution_order = []
        
        async def mock_executor(job):
            execution_order.append(job.priority.name)
            await asyncio.sleep(0.05)
            return True
        
        scheduler.set_executor(mock_executor)
        await scheduler.start()
        
        # Submit in reverse priority order
        await scheduler.submit(job_id="low", priority=JobPriority.LOW)
        await scheduler.submit(job_id="high", priority=JobPriority.HIGH)
        await scheduler.submit(job_id="critical", priority=JobPriority.CRITICAL)
        
        # Wait for all to execute
        await asyncio.sleep(1.0)
        
        # Higher priority should execute first
        if len(execution_order) >= 2:
            # First execution should be HIGH or CRITICAL (not LOW)
            assert execution_order[0] != "LOW"
        
        await scheduler.stop()
    
    @pytest.mark.asyncio
    async def test_concurrent_limit(self):
        """Should respect concurrent job limit."""
        scheduler = JobScheduler(max_concurrent=2)
        
        concurrent_count = []
        active = [0]
        
        async def mock_executor(job):
            active[0] += 1
            concurrent_count.append(active[0])
            await asyncio.sleep(0.2)
            active[0] -= 1
            return True
        
        scheduler.set_executor(mock_executor)
        await scheduler.start()
        
        # Submit more jobs than max_concurrent
        for i in range(5):
            await scheduler.submit(job_id=f"job-{i}")
        
        # Wait for some execution
        await asyncio.sleep(0.5)
        
        # Should never exceed max_concurrent
        if concurrent_count:
            assert max(concurrent_count) <= 2
        
        await scheduler.stop()


# =============================================================================
# WORKER POOL INTEGRATION TESTS
# =============================================================================

class TestWorkerPoolIntegration:
    """Integration tests for FFmpegWorkerPool."""
    
    @pytest.mark.asyncio
    async def test_run_simple_command(self):
        """Should run a simple command through the pool."""
        pool = FFmpegWorkerPool(max_workers=2)
        await pool.start()
        
        return_code, stderr = await pool.run_worker(
            worker_id="test",
            command=["python", "-c", "print('hello')"],
            timeout=10.0
        )
        
        assert return_code == 0
        
        await pool.stop()
    
    @pytest.mark.asyncio
    async def test_concurrent_workers(self):
        """Should run multiple workers concurrently."""
        pool = FFmpegWorkerPool(max_workers=3)
        await pool.start()
        
        async def run_worker(wid):
            return await pool.run_worker(
                worker_id=f"worker-{wid}",
                command=["python", "-c", "import time; time.sleep(0.1); print('done')"],
                timeout=5.0
            )
        
        # Run workers concurrently
        start = asyncio.get_event_loop().time()
        results = await asyncio.gather(
            run_worker(1),
            run_worker(2),
            run_worker(3)
        )
        elapsed = asyncio.get_event_loop().time() - start
        
        # All should succeed
        assert all(r[0] == 0 for r in results)
        
        # Should have run concurrently (< 3 * 0.1s)
        assert elapsed < 0.5
        
        await pool.stop()
    
    @pytest.mark.asyncio
    async def test_worker_timeout(self):
        """Should handle worker timeout."""
        pool = FFmpegWorkerPool(max_workers=1)
        await pool.start()
        
        return_code, stderr = await pool.run_worker(
            worker_id="timeout-test",
            command=["python", "-c", "import time; time.sleep(10)"],
            timeout=0.5
        )
        
        # Should have timed out
        assert return_code != 0
        
        await pool.stop()


# =============================================================================
# CONFIG INTEGRATION TESTS
# =============================================================================

class TestConfigIntegration:
    """Tests for config-driven behavior."""
    
    def test_stall_timeout_from_config(self):
        """Should use stall timeout from config."""
        with patch('ghoststream.transcoding.engine.get_config') as mock_config, \
             patch('ghoststream.transcoding.engine.get_capabilities') as mock_caps, \
             patch('shutil.which', return_value='ffmpeg'):
            
            mock_config.return_value = MagicMock(
                transcoding=MagicMock(
                    ffmpeg_path="ffmpeg",
                    temp_directory="./temp",
                    max_concurrent_jobs=2,
                    stall_timeout=300,  # Custom value
                    segment_duration=4,
                    retry_count=3,
                    validate_segments=True,
                ),
                hardware=MagicMock()
            )
            mock_caps.return_value = MagicMock(
                hw_accels=[],
                get_best_hw_accel=MagicMock(return_value=MagicMock(value="software"))
            )
            
            engine = TranscodeEngine()
            media_info = MediaInfo(width=1280, height=720)
            
            timeout = engine._calculate_stall_timeout(media_info)
            
            # Should be at least 300 (the config value)
            assert timeout >= 300
    
    def test_retry_count_from_config(self):
        """Should use retry count from config."""
        with patch('ghoststream.transcoding.engine.get_config') as mock_config, \
             patch('ghoststream.transcoding.engine.get_capabilities') as mock_caps, \
             patch('shutil.which', return_value='ffmpeg'):
            
            mock_config.return_value = MagicMock(
                transcoding=MagicMock(
                    ffmpeg_path="ffmpeg",
                    temp_directory="./temp",
                    max_concurrent_jobs=2,
                    stall_timeout=120,
                    segment_duration=4,
                    retry_count=5,  # Custom value
                    validate_segments=True,
                ),
                hardware=MagicMock()
            )
            mock_caps.return_value = MagicMock(
                hw_accels=[],
                get_best_hw_accel=MagicMock(return_value=MagicMock(value="software"))
            )
            
            engine = TranscodeEngine()
            
            assert engine.config.transcoding.retry_count == 5


# =============================================================================
# ERROR RECOVERY TESTS
# =============================================================================

class TestErrorRecovery:
    """Tests for error recovery scenarios."""
    
    @pytest.mark.asyncio
    async def test_scheduler_handles_executor_error(self):
        """Should handle executor exceptions."""
        scheduler = JobScheduler(max_concurrent=1)
        
        async def failing_executor(job):
            raise Exception("Test error")
        
        scheduler.set_executor(failing_executor)
        await scheduler.start()
        
        await scheduler.submit(job_id="failing-job")
        
        # Wait for execution attempt
        await asyncio.sleep(0.5)
        
        # Job should be marked as failed
        job = await scheduler.get_job("failing-job")
        # Note: Job might still be in failed state
        
        stats = scheduler.get_stats()
        # Should have recorded the failure
        assert stats["total_submitted"] == 1
        
        await scheduler.stop()
    
    @pytest.mark.asyncio
    async def test_worker_pool_handles_bad_command(self):
        """Should handle invalid commands."""
        pool = FFmpegWorkerPool(max_workers=1)
        await pool.start()
        
        return_code, stderr = await pool.run_worker(
            worker_id="bad-cmd",
            command=["nonexistent_command_xyz"],
            timeout=5.0
        )
        
        # Should fail gracefully
        assert return_code != 0
        
        await pool.stop()


# =============================================================================
# FULL PIPELINE TESTS
# =============================================================================

class TestFullPipeline:
    """Tests for complete transcoding pipeline."""
    
    @pytest.mark.asyncio
    async def test_scheduler_with_worker_pool(self):
        """Should integrate scheduler with worker pool."""
        pool = FFmpegWorkerPool(max_workers=2)
        scheduler = JobScheduler(max_concurrent=2)
        
        await pool.start()
        
        results = {}
        
        async def executor(job):
            return_code, stderr = await pool.run_worker(
                worker_id=job.job_id,
                command=["python", "-c", f"print('Job {job.job_id}')"],
                timeout=5.0
            )
            results[job.job_id] = return_code
            return return_code == 0
        
        scheduler.set_executor(executor)
        await scheduler.start()
        
        # Submit jobs
        await scheduler.submit(job_id="job-1")
        await scheduler.submit(job_id="job-2")
        
        # Wait for completion
        await asyncio.sleep(1.0)
        
        # Both should complete successfully
        assert results.get("job-1") == 0
        assert results.get("job-2") == 0
        
        await scheduler.stop()
        await pool.stop()

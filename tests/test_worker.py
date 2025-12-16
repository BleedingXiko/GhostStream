"""
Tests for GhostStream FFmpeg Worker Pool.

Tests cover:
- FFmpegWorker lifecycle
- WorkerPool concurrency management
- Worker statistics tracking
- Graceful shutdown
"""

import asyncio
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime
from pathlib import Path

from ghoststream.transcoding.worker import (
    WorkerState,
    WorkerStats,
    FFmpegWorker,
    FFmpegWorkerPool,
    get_worker_pool,
    init_worker_pool,
    shutdown_worker_pool,
)


# =============================================================================
# WORKER STATE TESTS
# =============================================================================

class TestWorkerState:
    """Tests for WorkerState enum."""
    
    def test_all_states_exist(self):
        """Should have all expected states."""
        states = [
            WorkerState.IDLE,
            WorkerState.STARTING,
            WorkerState.RUNNING,
            WorkerState.STOPPING,
            WorkerState.STOPPED,
            WorkerState.ERROR
        ]
        assert len(states) == 6
    
    def test_state_values(self):
        """State values should be strings."""
        assert WorkerState.IDLE == "idle"
        assert WorkerState.RUNNING == "running"
        assert WorkerState.ERROR == "error"


# =============================================================================
# WORKER STATS TESTS
# =============================================================================

class TestWorkerStats:
    """Tests for WorkerStats dataclass."""
    
    def test_default_stats(self):
        """Should have sensible defaults."""
        stats = WorkerStats()
        assert stats.start_time is None
        assert stats.end_time is None
        assert stats.bytes_processed == 0
        assert stats.frames_processed == 0
        assert stats.return_code is None
    
    def test_duration_not_started(self):
        """Duration should be 0 if not started."""
        stats = WorkerStats()
        assert stats.duration_seconds == 0.0
    
    def test_duration_running(self):
        """Duration should calculate from start time."""
        stats = WorkerStats(start_time=datetime.utcnow())
        # Small sleep to ensure duration > 0
        import time
        time.sleep(0.01)
        assert stats.duration_seconds > 0
    
    def test_duration_completed(self):
        """Duration should use end time if available."""
        start = datetime(2024, 1, 1, 12, 0, 0)
        end = datetime(2024, 1, 1, 12, 0, 30)
        stats = WorkerStats(start_time=start, end_time=end)
        assert abs(stats.duration_seconds - 30.0) < 0.1


# =============================================================================
# FFMPEG WORKER TESTS
# =============================================================================

class TestFFmpegWorker:
    """Tests for FFmpegWorker class."""
    
    def test_create_worker(self):
        """Should create worker with correct initial state."""
        worker = FFmpegWorker(
            worker_id="test-1",
            command=["ffmpeg", "-version"]
        )
        assert worker.worker_id == "test-1"
        assert worker.state == WorkerState.IDLE
        assert worker.process is None
    
    def test_is_running_idle(self):
        """Should not be running when idle."""
        worker = FFmpegWorker(worker_id="test", command=["echo"])
        assert worker.is_running() is False
    
    @pytest.mark.asyncio
    async def test_start_worker(self):
        """Should start worker process."""
        worker = FFmpegWorker(
            worker_id="test",
            command=["python", "-c", "print('hello')"]
        )
        
        result = await worker.start()
        assert result is True
        assert worker.state == WorkerState.RUNNING
        assert worker.process is not None
        
        # Cleanup
        await worker.wait()
    
    @pytest.mark.asyncio
    async def test_start_already_running(self):
        """Should not start if already running."""
        worker = FFmpegWorker(
            worker_id="test",
            command=["python", "-c", "import time; time.sleep(1)"]
        )
        
        await worker.start()
        result = await worker.start()  # Try to start again
        
        assert result is False
        
        # Cleanup
        await worker.stop()
    
    @pytest.mark.asyncio
    async def test_stop_worker(self):
        """Should stop running worker."""
        worker = FFmpegWorker(
            worker_id="test",
            command=["python", "-c", "import time; time.sleep(10)"]
        )
        
        await worker.start()
        return_code = await worker.stop(timeout=5.0)
        
        assert worker.state == WorkerState.STOPPED
        assert worker.stats.end_time is not None
    
    @pytest.mark.asyncio
    async def test_wait_for_completion(self):
        """Should wait for process to complete."""
        worker = FFmpegWorker(
            worker_id="test",
            command=["python", "-c", "print('done')"]
        )
        
        await worker.start()
        return_code = await worker.wait()
        
        assert worker.state == WorkerState.STOPPED
        assert return_code == 0
    
    def test_get_stderr_empty(self):
        """Should return empty string if no stderr."""
        worker = FFmpegWorker(worker_id="test", command=["echo"])
        assert worker.get_stderr() == ""
    
    def test_get_stderr_with_content(self):
        """Should return accumulated stderr."""
        worker = FFmpegWorker(worker_id="test", command=["echo"])
        worker._stderr_buffer = ["line1\n", "line2\n"]
        assert worker.get_stderr() == "line1\nline2\n"


# =============================================================================
# WORKER POOL TESTS
# =============================================================================

class TestFFmpegWorkerPool:
    """Tests for FFmpegWorkerPool class."""
    
    def test_create_pool(self):
        """Should create pool with max workers."""
        pool = FFmpegWorkerPool(max_workers=4)
        assert pool.max_workers == 4
        assert pool._running is False
    
    @pytest.mark.asyncio
    async def test_start_pool(self):
        """Should start the pool."""
        pool = FFmpegWorkerPool(max_workers=2)
        await pool.start()
        
        assert pool._running is True
        
        await pool.stop()
    
    @pytest.mark.asyncio
    async def test_stop_pool(self):
        """Should stop all workers."""
        pool = FFmpegWorkerPool(max_workers=2)
        await pool.start()
        await pool.stop()
        
        assert pool._running is False
    
    @pytest.mark.asyncio
    async def test_create_worker_in_pool(self):
        """Should create and track worker."""
        pool = FFmpegWorkerPool(max_workers=2)
        await pool.start()
        
        worker = await pool.create_worker(
            "test-worker",
            ["python", "-c", "print('hello')"]
        )
        
        assert worker is not None
        assert worker.worker_id == "test-worker"
        assert "test-worker" in pool._workers
        
        await pool.stop()
    
    @pytest.mark.asyncio
    async def test_create_duplicate_worker(self):
        """Should return existing worker for duplicate ID."""
        pool = FFmpegWorkerPool(max_workers=2)
        await pool.start()
        
        worker1 = await pool.create_worker("test", ["echo"])
        worker2 = await pool.create_worker("test", ["echo"])
        
        assert worker1 is worker2
        
        await pool.stop()
    
    @pytest.mark.asyncio
    async def test_acquire_slot(self):
        """Should acquire slot from semaphore."""
        pool = FFmpegWorkerPool(max_workers=2)
        await pool.start()
        
        result = await pool.acquire_slot(timeout=1.0)
        assert result is True
        
        pool.release_slot()
        await pool.stop()
    
    @pytest.mark.asyncio
    async def test_acquire_slot_timeout(self):
        """Should timeout if no slots available."""
        pool = FFmpegWorkerPool(max_workers=1)
        await pool.start()
        
        # Acquire the only slot
        await pool.acquire_slot()
        
        # Try to acquire another (should timeout)
        result = await pool.acquire_slot(timeout=0.1)
        assert result is False
        
        pool.release_slot()
        await pool.stop()
    
    def test_get_active_count_empty(self):
        """Should return 0 when no active workers."""
        pool = FFmpegWorkerPool(max_workers=2)
        assert pool.get_active_count() == 0
    
    @pytest.mark.asyncio
    async def test_get_stats(self):
        """Should return pool statistics."""
        pool = FFmpegWorkerPool(max_workers=4)
        await pool.start()
        
        stats = pool.get_stats()
        
        assert stats["max_workers"] == 4
        assert stats["active_workers"] == 0
        assert stats["running"] is True
        
        await pool.stop()
    
    @pytest.mark.asyncio
    async def test_run_worker(self):
        """Should run command and return result."""
        pool = FFmpegWorkerPool(max_workers=2)
        await pool.start()
        
        return_code, stderr = await pool.run_worker(
            "test-run",
            ["python", "-c", "print('hello')"],
            timeout=10.0
        )
        
        assert return_code == 0
        
        await pool.stop()


# =============================================================================
# GLOBAL INSTANCE TESTS
# =============================================================================

class TestGlobalWorkerPool:
    """Tests for global worker pool functions."""
    
    def test_get_worker_pool_creates_instance(self):
        """Should create singleton instance."""
        # Reset global
        import ghoststream.transcoding.worker as worker_module
        worker_module._worker_pool = None
        
        pool1 = get_worker_pool(max_workers=2)
        pool2 = get_worker_pool(max_workers=4)  # Should ignore this
        
        assert pool1 is pool2
        assert pool1.max_workers == 2
        
        # Cleanup
        worker_module._worker_pool = None
    
    @pytest.mark.asyncio
    async def test_init_worker_pool(self):
        """Should initialize and start pool."""
        import ghoststream.transcoding.worker as worker_module
        worker_module._worker_pool = None
        
        pool = await init_worker_pool(max_workers=2)
        
        assert pool._running is True
        
        await shutdown_worker_pool()
    
    @pytest.mark.asyncio
    async def test_shutdown_worker_pool(self):
        """Should shutdown global pool."""
        import ghoststream.transcoding.worker as worker_module
        worker_module._worker_pool = None
        
        await init_worker_pool(max_workers=2)
        await shutdown_worker_pool()
        
        assert worker_module._worker_pool is None

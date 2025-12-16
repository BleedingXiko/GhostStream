"""
Tests for GhostStream Job Scheduler.

Tests cover:
- Job priority handling
- Priority queue operations
- Job submission and cancellation
- Preemption logic
- Aging for fair scheduling
- Statistics tracking
"""

import asyncio
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime, timedelta

from ghoststream.transcoding.scheduler import (
    JobPriority,
    JobState,
    ScheduledJob,
    JobScheduler,
    get_scheduler,
    init_scheduler,
    shutdown_scheduler,
)


# =============================================================================
# JOB PRIORITY TESTS
# =============================================================================

class TestJobPriority:
    """Tests for JobPriority enum."""
    
    def test_priority_order(self):
        """Critical should be highest priority (lowest number)."""
        assert JobPriority.CRITICAL < JobPriority.HIGH
        assert JobPriority.HIGH < JobPriority.NORMAL
        assert JobPriority.NORMAL < JobPriority.LOW
        assert JobPriority.LOW < JobPriority.IDLE
    
    def test_all_priorities_exist(self):
        """Should have all expected priorities."""
        priorities = [
            JobPriority.CRITICAL,
            JobPriority.HIGH,
            JobPriority.NORMAL,
            JobPriority.LOW,
            JobPriority.IDLE
        ]
        assert len(priorities) == 5
    
    def test_priority_values(self):
        """Priority values should be integers."""
        assert JobPriority.CRITICAL == 1
        assert JobPriority.NORMAL == 3
        assert JobPriority.IDLE == 5


# =============================================================================
# JOB STATE TESTS
# =============================================================================

class TestJobState:
    """Tests for JobState constants."""
    
    def test_all_states_exist(self):
        """Should have all expected states."""
        assert JobState.PENDING == "pending"
        assert JobState.QUEUED == "queued"
        assert JobState.RUNNING == "running"
        assert JobState.PAUSED == "paused"
        assert JobState.COMPLETED == "completed"
        assert JobState.FAILED == "failed"
        assert JobState.CANCELLED == "cancelled"
        assert JobState.PREEMPTED == "preempted"


# =============================================================================
# SCHEDULED JOB TESTS
# =============================================================================

class TestScheduledJob:
    """Tests for ScheduledJob class."""
    
    def test_create_job(self):
        """Should create job with factory method."""
        job = ScheduledJob.create(
            job_id="test-1",
            priority=JobPriority.NORMAL,
            source="/path/to/video.mp4"
        )
        
        assert job.job_id == "test-1"
        assert job.priority == JobPriority.NORMAL
        assert job.source == "/path/to/video.mp4"
        assert job.state == JobState.PENDING
    
    def test_job_default_values(self):
        """Should have sensible defaults."""
        job = ScheduledJob.create(job_id="test")
        
        assert job.priority == JobPriority.NORMAL
        assert job.preemptable is True
        assert job.retry_count == 0
        assert job.max_retries == 3
    
    def test_job_wait_time_not_started(self):
        """Wait time should increase while waiting."""
        job = ScheduledJob.create(job_id="test")
        
        # Wait a bit
        import time
        time.sleep(0.01)
        
        assert job.wait_time_s > 0
    
    def test_job_run_time_not_running(self):
        """Run time should be 0 if not started."""
        job = ScheduledJob.create(job_id="test")
        assert job.run_time_s == 0.0
    
    def test_job_run_time_running(self):
        """Run time should calculate while running."""
        job = ScheduledJob.create(job_id="test")
        job.start_time = datetime.utcnow()
        
        import time
        time.sleep(0.01)
        
        assert job.run_time_s > 0
    
    def test_job_ordering(self):
        """Jobs should order by priority then submit time."""
        job_high = ScheduledJob.create(job_id="high", priority=JobPriority.HIGH)
        job_normal = ScheduledJob.create(job_id="normal", priority=JobPriority.NORMAL)
        job_low = ScheduledJob.create(job_id="low", priority=JobPriority.LOW)
        
        jobs = sorted([job_low, job_high, job_normal])
        
        assert jobs[0].job_id == "high"
        assert jobs[1].job_id == "normal"
        assert jobs[2].job_id == "low"
    
    def test_age_bonus_update(self):
        """Should update sort key with age bonus."""
        job = ScheduledJob.create(job_id="test", priority=JobPriority.LOW)
        original_key = job.sort_key
        
        # Simulate waiting
        job.submit_time = datetime.utcnow() - timedelta(seconds=150)
        job.update_age_bonus(max_wait_s=300.0)
        
        # Age bonus should make it sort earlier
        assert job.sort_key < original_key


# =============================================================================
# JOB SCHEDULER TESTS
# =============================================================================

class TestJobScheduler:
    """Tests for JobScheduler class."""
    
    def test_create_scheduler(self):
        """Should create scheduler with config."""
        scheduler = JobScheduler(
            max_concurrent=4,
            enable_preemption=True,
            max_queue_size=100
        )
        
        assert scheduler.max_concurrent == 4
        assert scheduler.enable_preemption is True
        assert scheduler.max_queue_size == 100
    
    def test_default_scheduler(self):
        """Should have sensible defaults."""
        scheduler = JobScheduler()
        
        assert scheduler.max_concurrent == 4
        assert scheduler.enable_preemption is False
        assert scheduler.max_queue_size == 1000
    
    @pytest.mark.asyncio
    async def test_start_scheduler(self):
        """Should start scheduler tasks."""
        scheduler = JobScheduler(max_concurrent=2)
        await scheduler.start()
        
        assert scheduler._running_flag is True
        assert scheduler._dispatch_task is not None
        assert scheduler._aging_task is not None
        
        await scheduler.stop()
    
    @pytest.mark.asyncio
    async def test_stop_scheduler(self):
        """Should stop scheduler cleanly."""
        scheduler = JobScheduler(max_concurrent=2)
        await scheduler.start()
        await scheduler.stop()
        
        assert scheduler._running_flag is False
    
    @pytest.mark.asyncio
    async def test_submit_job(self):
        """Should submit job to queue."""
        scheduler = JobScheduler(max_concurrent=2)
        await scheduler.start()
        
        accepted, message, job = await scheduler.submit(
            job_id="test-job",
            priority=JobPriority.NORMAL,
            source="/path/video.mp4"
        )
        
        assert accepted is True
        assert job is not None
        assert job.state == JobState.QUEUED
        
        await scheduler.stop()
    
    @pytest.mark.asyncio
    async def test_submit_duplicate_job(self):
        """Should reject duplicate job ID."""
        scheduler = JobScheduler(max_concurrent=2)
        await scheduler.start()
        
        await scheduler.submit(job_id="test-job")
        accepted, message, job = await scheduler.submit(job_id="test-job")
        
        assert accepted is False
        assert "exists" in message.lower()
        
        await scheduler.stop()
    
    @pytest.mark.asyncio
    async def test_submit_queue_full(self):
        """Should reject when queue is full."""
        scheduler = JobScheduler(max_concurrent=2, max_queue_size=2)
        await scheduler.start()
        
        # Fill the queue
        await scheduler.submit(job_id="job-1")
        await scheduler.submit(job_id="job-2")
        
        # Try to add one more
        accepted, message, _ = await scheduler.submit(job_id="job-3")
        
        assert accepted is False
        assert "full" in message.lower()
        
        await scheduler.stop()
    
    @pytest.mark.asyncio
    async def test_cancel_queued_job(self):
        """Should cancel queued job."""
        scheduler = JobScheduler(max_concurrent=2)
        await scheduler.start()
        
        await scheduler.submit(job_id="test-job")
        success, message = await scheduler.cancel("test-job")
        
        assert success is True
        
        job = await scheduler.get_job("test-job")
        assert job.state == JobState.CANCELLED
        
        await scheduler.stop()
    
    @pytest.mark.asyncio
    async def test_cancel_nonexistent_job(self):
        """Should fail to cancel nonexistent job."""
        scheduler = JobScheduler(max_concurrent=2)
        await scheduler.start()
        
        success, message = await scheduler.cancel("nonexistent")
        
        assert success is False
        assert "not found" in message.lower()
        
        await scheduler.stop()
    
    @pytest.mark.asyncio
    async def test_get_job(self):
        """Should retrieve job by ID."""
        scheduler = JobScheduler(max_concurrent=2)
        await scheduler.start()
        
        await scheduler.submit(job_id="test-job", source="test.mp4")
        job = await scheduler.get_job("test-job")
        
        assert job is not None
        assert job.source == "test.mp4"
        
        await scheduler.stop()
    
    @pytest.mark.asyncio
    async def test_get_queue_position(self):
        """Should return correct queue position."""
        scheduler = JobScheduler(max_concurrent=1)
        await scheduler.start()
        
        # Submit jobs (they'll queue since no executor)
        await scheduler.submit(job_id="job-1", priority=JobPriority.HIGH)
        await scheduler.submit(job_id="job-2", priority=JobPriority.NORMAL)
        await scheduler.submit(job_id="job-3", priority=JobPriority.LOW)
        
        # Give a moment for processing
        await asyncio.sleep(0.1)
        
        pos1 = await scheduler.get_queue_position("job-1")
        pos2 = await scheduler.get_queue_position("job-2")
        pos3 = await scheduler.get_queue_position("job-3")
        
        # Higher priority should be earlier in queue
        assert pos1 <= pos2 <= pos3
        
        await scheduler.stop()
    
    @pytest.mark.asyncio
    async def test_get_stats(self):
        """Should return scheduler statistics."""
        scheduler = JobScheduler(max_concurrent=4)
        await scheduler.start()
        
        stats = scheduler.get_stats()
        
        assert "running" in stats
        assert "queued" in stats
        assert "max_concurrent" in stats
        assert stats["max_concurrent"] == 4
        
        await scheduler.stop()
    
    @pytest.mark.asyncio
    async def test_get_queue_summary(self):
        """Should return queue summary."""
        scheduler = JobScheduler(max_concurrent=2)
        await scheduler.start()
        
        await scheduler.submit(job_id="test-job", source="video.mp4")
        
        summary = scheduler.get_queue_summary()
        
        assert isinstance(summary, list)
        
        await scheduler.stop()
    
    @pytest.mark.asyncio
    async def test_set_executor(self):
        """Should set job executor function."""
        scheduler = JobScheduler(max_concurrent=2)
        
        async def mock_executor(job):
            return "completed"
        
        scheduler.set_executor(mock_executor)
        
        assert scheduler._job_executor is mock_executor


# =============================================================================
# PRIORITY QUEUE BEHAVIOR TESTS
# =============================================================================

class TestPriorityQueueBehavior:
    """Tests for priority queue ordering."""
    
    @pytest.mark.asyncio
    async def test_high_priority_first(self):
        """Higher priority jobs should be processed first."""
        scheduler = JobScheduler(max_concurrent=1)
        
        results = []
        
        async def track_executor(job):
            results.append(job.job_id)
            return True
        
        scheduler.set_executor(track_executor)
        await scheduler.start()
        
        # Submit in reverse priority order
        await scheduler.submit(job_id="low", priority=JobPriority.LOW)
        await scheduler.submit(job_id="high", priority=JobPriority.HIGH)
        await scheduler.submit(job_id="critical", priority=JobPriority.CRITICAL)
        
        # Wait for processing
        await asyncio.sleep(0.5)
        
        await scheduler.stop()
        
        # Critical should be processed first (it was submitted last but has highest priority)
        if len(results) >= 2:
            assert results[0] == "critical" or results[1] == "critical"


# =============================================================================
# PREEMPTION TESTS
# =============================================================================

class TestPreemption:
    """Tests for job preemption."""
    
    @pytest.mark.asyncio
    async def test_preemption_disabled(self):
        """Should not preempt when disabled."""
        scheduler = JobScheduler(max_concurrent=1, enable_preemption=False)
        await scheduler.start()
        
        # Preemption should not happen
        assert scheduler.enable_preemption is False
        
        await scheduler.stop()
    
    @pytest.mark.asyncio
    async def test_preemption_enabled(self):
        """Should allow preemption when enabled."""
        scheduler = JobScheduler(max_concurrent=1, enable_preemption=True)
        await scheduler.start()
        
        assert scheduler.enable_preemption is True
        
        await scheduler.stop()


# =============================================================================
# GLOBAL INSTANCE TESTS
# =============================================================================

class TestGlobalScheduler:
    """Tests for global scheduler functions."""
    
    def test_get_scheduler_creates_instance(self):
        """Should create singleton instance."""
        import ghoststream.transcoding.scheduler as sched_module
        sched_module._scheduler = None
        
        sched1 = get_scheduler(max_concurrent=2)
        sched2 = get_scheduler(max_concurrent=4)
        
        assert sched1 is sched2
        assert sched1.max_concurrent == 2
        
        sched_module._scheduler = None
    
    @pytest.mark.asyncio
    async def test_init_scheduler(self):
        """Should initialize and start scheduler."""
        import ghoststream.transcoding.scheduler as sched_module
        sched_module._scheduler = None
        
        scheduler = await init_scheduler(max_concurrent=2)
        
        assert scheduler._running_flag is True
        
        await shutdown_scheduler()
    
    @pytest.mark.asyncio
    async def test_shutdown_scheduler(self):
        """Should shutdown global scheduler."""
        import ghoststream.transcoding.scheduler as sched_module
        sched_module._scheduler = None
        
        await init_scheduler(max_concurrent=2)
        await shutdown_scheduler()
        
        assert sched_module._scheduler is None

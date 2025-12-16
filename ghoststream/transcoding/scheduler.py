"""
Global Job Scheduler for GhostStream.

Provides enterprise-grade job scheduling with:
- Central concurrency limits
- Priority queue with multiple priority levels
- Optional preemption for high-priority jobs
- Fair scheduling with aging
- Resource-aware scheduling
"""

import asyncio
import heapq
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import IntEnum
from typing import Optional, Dict, List, Callable, Any, Tuple, Set
from uuid import uuid4

logger = logging.getLogger(__name__)


class JobPriority(IntEnum):
    """Job priority levels (lower number = higher priority)."""
    CRITICAL = 1      # System/admin initiated, preemptable
    HIGH = 2          # User-initiated, time-sensitive
    NORMAL = 3        # Standard user requests
    LOW = 4           # Background tasks
    IDLE = 5          # Fill-in work when system is idle


class JobState(str):
    """Job state constants."""
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PREEMPTED = "preempted"


@dataclass(order=True)
class ScheduledJob:
    """
    A job in the scheduler queue.
    
    Uses dataclass ordering for heap operations.
    Priority is (priority, -age_bonus, submit_time) for fair scheduling.
    """
    sort_key: Tuple[int, float, float] = field(compare=True)
    job_id: str = field(compare=False)
    priority: JobPriority = field(compare=False, default=JobPriority.NORMAL)
    state: str = field(compare=False, default=JobState.PENDING)
    submit_time: datetime = field(compare=False, default_factory=datetime.utcnow)
    start_time: Optional[datetime] = field(compare=False, default=None)
    end_time: Optional[datetime] = field(compare=False, default=None)
    
    # Job metadata
    source: str = field(compare=False, default="")
    estimated_duration_s: float = field(compare=False, default=0.0)
    complexity_score: float = field(compare=False, default=1.0)
    user_id: Optional[str] = field(compare=False, default=None)
    
    # Execution tracking
    retry_count: int = field(compare=False, default=0)
    max_retries: int = field(compare=False, default=3)
    preemptable: bool = field(compare=False, default=True)
    preemption_count: int = field(compare=False, default=0)
    
    # Callbacks
    on_start: Optional[Callable[["ScheduledJob"], Any]] = field(compare=False, default=None)
    on_complete: Optional[Callable[["ScheduledJob", bool], Any]] = field(compare=False, default=None)
    on_preempt: Optional[Callable[["ScheduledJob"], Any]] = field(compare=False, default=None)
    
    # Result storage
    result: Any = field(compare=False, default=None)
    error: Optional[str] = field(compare=False, default=None)
    
    @classmethod
    def create(
        cls,
        job_id: str,
        priority: JobPriority = JobPriority.NORMAL,
        source: str = "",
        estimated_duration_s: float = 0.0,
        complexity_score: float = 1.0,
        user_id: Optional[str] = None,
        preemptable: bool = True,
        on_start: Optional[Callable] = None,
        on_complete: Optional[Callable] = None,
        on_preempt: Optional[Callable] = None,
    ) -> "ScheduledJob":
        """Create a new scheduled job with proper sort key."""
        submit_time = datetime.utcnow()
        # Sort key: (priority, -age_bonus (starts at 0), submit_timestamp)
        sort_key = (int(priority), 0.0, submit_time.timestamp())
        
        return cls(
            sort_key=sort_key,
            job_id=job_id,
            priority=priority,
            submit_time=submit_time,
            source=source,
            estimated_duration_s=estimated_duration_s,
            complexity_score=complexity_score,
            user_id=user_id,
            preemptable=preemptable,
            on_start=on_start,
            on_complete=on_complete,
            on_preempt=on_preempt,
        )
    
    @property
    def wait_time_s(self) -> float:
        """Time spent waiting in queue."""
        if self.start_time:
            return (self.start_time - self.submit_time).total_seconds()
        return (datetime.utcnow() - self.submit_time).total_seconds()
    
    @property
    def run_time_s(self) -> float:
        """Time spent running."""
        if not self.start_time:
            return 0.0
        end = self.end_time or datetime.utcnow()
        return (end - self.start_time).total_seconds()
    
    def update_age_bonus(self, max_wait_s: float = 300.0) -> None:
        """
        Update sort key with age bonus for fair scheduling.
        
        Jobs waiting longer get priority boost to prevent starvation.
        """
        wait_time = self.wait_time_s
        # Age bonus: up to 1 priority level after max_wait_s
        age_bonus = min(wait_time / max_wait_s, 1.0)
        self.sort_key = (int(self.priority), -age_bonus, self.submit_time.timestamp())


class JobScheduler:
    """
    Global job scheduler with priority queue and preemption support.
    
    Features:
    - Priority-based scheduling with aging for fairness
    - Configurable concurrency limits
    - Optional preemption for high-priority jobs
    - Resource-aware scheduling
    - Comprehensive statistics tracking
    """
    
    def __init__(
        self,
        max_concurrent: int = 4,
        enable_preemption: bool = False,
        max_queue_size: int = 1000,
        aging_interval_s: float = 10.0,
        max_wait_for_priority_boost_s: float = 300.0,
    ):
        """
        Initialize the scheduler.
        
        Args:
            max_concurrent: Maximum concurrent jobs
            enable_preemption: Allow high-priority jobs to preempt lower priority
            max_queue_size: Maximum jobs in queue
            aging_interval_s: How often to update age bonuses
            max_wait_for_priority_boost_s: Wait time to get full priority boost
        """
        self.max_concurrent = max_concurrent
        self.enable_preemption = enable_preemption
        self.max_queue_size = max_queue_size
        self.aging_interval_s = aging_interval_s
        self.max_wait_for_priority_boost_s = max_wait_for_priority_boost_s
        
        # Job storage
        self._queue: List[ScheduledJob] = []  # Priority heap
        self._running: Dict[str, ScheduledJob] = {}
        self._completed: Dict[str, ScheduledJob] = {}
        self._all_jobs: Dict[str, ScheduledJob] = {}
        
        # Synchronization
        self._lock = asyncio.Lock()
        self._queue_not_empty = asyncio.Condition()
        self._slot_available = asyncio.Semaphore(max_concurrent)
        
        # State
        self._running_flag = False
        self._dispatch_task: Optional[asyncio.Task] = None
        self._aging_task: Optional[asyncio.Task] = None
        
        # Statistics
        self._stats = {
            "total_submitted": 0,
            "total_completed": 0,
            "total_failed": 0,
            "total_cancelled": 0,
            "total_preempted": 0,
            "total_wait_time_s": 0.0,
            "total_run_time_s": 0.0,
        }
        
        # Job execution function (set externally)
        self._job_executor: Optional[Callable[[ScheduledJob], Any]] = None
    
    def set_executor(self, executor: Callable[[ScheduledJob], Any]) -> None:
        """Set the function that executes jobs."""
        self._job_executor = executor
    
    async def start(self) -> None:
        """Start the scheduler."""
        if self._running_flag:
            return
        
        self._running_flag = True
        self._dispatch_task = asyncio.create_task(self._dispatch_loop())
        self._aging_task = asyncio.create_task(self._aging_loop())
        
        logger.info(f"[Scheduler] Started with max_concurrent={self.max_concurrent}, "
                   f"preemption={'enabled' if self.enable_preemption else 'disabled'}")
    
    async def stop(self, timeout: float = 30.0) -> None:
        """Stop the scheduler and wait for running jobs."""
        self._running_flag = False
        
        # Cancel background tasks
        if self._dispatch_task:
            self._dispatch_task.cancel()
            try:
                await self._dispatch_task
            except asyncio.CancelledError:
                pass
        
        if self._aging_task:
            self._aging_task.cancel()
            try:
                await self._aging_task
            except asyncio.CancelledError:
                pass
        
        # Wait for running jobs with timeout
        if self._running:
            logger.info(f"[Scheduler] Waiting for {len(self._running)} running jobs...")
            start = time.time()
            while self._running and (time.time() - start) < timeout:
                await asyncio.sleep(0.5)
        
        # Cancel remaining jobs
        async with self._lock:
            for job in self._running.values():
                job.state = JobState.CANCELLED
                job.end_time = datetime.utcnow()
            self._running.clear()
            
            for job in self._queue:
                job.state = JobState.CANCELLED
            self._queue.clear()
        
        logger.info("[Scheduler] Stopped")
    
    async def submit(
        self,
        job_id: Optional[str] = None,
        priority: JobPriority = JobPriority.NORMAL,
        source: str = "",
        estimated_duration_s: float = 0.0,
        complexity_score: float = 1.0,
        user_id: Optional[str] = None,
        preemptable: bool = True,
        on_start: Optional[Callable] = None,
        on_complete: Optional[Callable] = None,
        on_preempt: Optional[Callable] = None,
    ) -> Tuple[bool, str, ScheduledJob]:
        """
        Submit a job to the scheduler.
        
        Returns:
            Tuple of (accepted, message, job)
        """
        if not self._running_flag:
            raise RuntimeError("Scheduler is not running")
        
        job_id = job_id or str(uuid4())
        
        async with self._lock:
            # Check queue size
            if len(self._queue) >= self.max_queue_size:
                return False, "Queue is full", None
            
            # Check for duplicate
            if job_id in self._all_jobs:
                return False, f"Job {job_id} already exists", self._all_jobs[job_id]
            
            # Create job
            job = ScheduledJob.create(
                job_id=job_id,
                priority=priority,
                source=source,
                estimated_duration_s=estimated_duration_s,
                complexity_score=complexity_score,
                user_id=user_id,
                preemptable=preemptable,
                on_start=on_start,
                on_complete=on_complete,
                on_preempt=on_preempt,
            )
            job.state = JobState.QUEUED
            
            # Add to queue and tracking
            heapq.heappush(self._queue, job)
            self._all_jobs[job_id] = job
            self._stats["total_submitted"] += 1
            
            queue_pos = len(self._queue)
            
        # Signal dispatcher
        async with self._queue_not_empty:
            self._queue_not_empty.notify()
        
        logger.info(f"[Scheduler] Job {job_id} submitted (priority={priority.name}, pos={queue_pos})")
        return True, f"Queued at position {queue_pos}", job
    
    async def cancel(self, job_id: str) -> Tuple[bool, str]:
        """Cancel a job."""
        async with self._lock:
            job = self._all_jobs.get(job_id)
            if not job:
                return False, "Job not found"
            
            if job.state in (JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED):
                return False, f"Job already in terminal state: {job.state}"
            
            if job.state == JobState.RUNNING:
                # Mark for cancellation, executor should check
                job.state = JobState.CANCELLED
                job.end_time = datetime.utcnow()
                if job_id in self._running:
                    del self._running[job_id]
                self._slot_available.release()
                self._stats["total_cancelled"] += 1
                return True, "Cancelled running job"
            
            # Remove from queue
            if job.state == JobState.QUEUED:
                job.state = JobState.CANCELLED
                # Rebuild heap without this job
                self._queue = [j for j in self._queue if j.job_id != job_id]
                heapq.heapify(self._queue)
                self._stats["total_cancelled"] += 1
                return True, "Cancelled queued job"
            
            return False, f"Cannot cancel job in state: {job.state}"
    
    async def get_job(self, job_id: str) -> Optional[ScheduledJob]:
        """Get job by ID."""
        async with self._lock:
            return self._all_jobs.get(job_id)
    
    async def get_queue_position(self, job_id: str) -> int:
        """Get job's position in queue (0 = running, -1 = not found)."""
        async with self._lock:
            job = self._all_jobs.get(job_id)
            if not job:
                return -1
            if job.state == JobState.RUNNING:
                return 0
            if job.state != JobState.QUEUED:
                return -1
            
            # Find position in heap
            for i, j in enumerate(sorted(self._queue)):
                if j.job_id == job_id:
                    return i + 1
            return -1
    
    async def _dispatch_loop(self) -> None:
        """Main dispatch loop - assigns jobs to execution slots."""
        while self._running_flag:
            try:
                # Wait for a slot
                await self._slot_available.acquire()
                
                # Get next job from queue
                job = await self._get_next_job()
                if not job:
                    self._slot_available.release()
                    await asyncio.sleep(0.1)
                    continue
                
                # Check for preemption opportunity
                if self.enable_preemption and job.priority <= JobPriority.HIGH:
                    preempted = await self._try_preempt(job)
                    if preempted:
                        self._slot_available.release()  # Slot freed by preemption
                
                # Start job execution
                asyncio.create_task(self._execute_job(job))
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[Scheduler] Dispatch error: {e}")
                self._slot_available.release()
                await asyncio.sleep(1.0)
    
    async def _get_next_job(self) -> Optional[ScheduledJob]:
        """Get the next job from the queue."""
        async with self._lock:
            while self._queue:
                job = heapq.heappop(self._queue)
                if job.state == JobState.QUEUED:
                    return job
            return None
    
    async def _try_preempt(self, high_priority_job: ScheduledJob) -> bool:
        """Try to preempt a lower priority job for a high priority one."""
        async with self._lock:
            # Find lowest priority preemptable job
            candidates = [
                (job_id, job) for job_id, job in self._running.items()
                if job.preemptable and job.priority > high_priority_job.priority
            ]
            
            if not candidates:
                return False
            
            # Preempt the lowest priority job
            candidates.sort(key=lambda x: (-x[1].priority.value, x[1].run_time_s))
            victim_id, victim = candidates[0]
            
            # Trigger preemption
            victim.state = JobState.PREEMPTED
            victim.preemption_count += 1
            del self._running[victim_id]
            
            # Re-queue the preempted job with a small priority boost
            victim.state = JobState.QUEUED
            victim.start_time = None
            heapq.heappush(self._queue, victim)
            
            self._stats["total_preempted"] += 1
            
            if victim.on_preempt:
                try:
                    victim.on_preempt(victim)
                except Exception as e:
                    logger.warning(f"[Scheduler] Preempt callback error: {e}")
            
            logger.info(f"[Scheduler] Preempted job {victim_id} for {high_priority_job.job_id}")
            return True
    
    async def _execute_job(self, job: ScheduledJob) -> None:
        """Execute a single job."""
        job.state = JobState.RUNNING
        job.start_time = datetime.utcnow()
        
        async with self._lock:
            self._running[job.job_id] = job
        
        # Call start callback
        if job.on_start:
            try:
                job.on_start(job)
            except Exception as e:
                logger.warning(f"[Scheduler] Start callback error: {e}")
        
        logger.debug(f"[Scheduler] Starting job {job.job_id}")
        
        success = False
        try:
            if self._job_executor:
                job.result = await self._job_executor(job)
                success = True
            else:
                job.error = "No executor configured"
                
        except asyncio.CancelledError:
            job.state = JobState.CANCELLED
            job.error = "Cancelled"
        except Exception as e:
            job.state = JobState.FAILED
            job.error = str(e)
            logger.error(f"[Scheduler] Job {job.job_id} failed: {e}")
        
        # Finalize job
        job.end_time = datetime.utcnow()
        
        async with self._lock:
            if job.job_id in self._running:
                del self._running[job.job_id]
            
            if job.state == JobState.RUNNING:
                job.state = JobState.COMPLETED if success else JobState.FAILED
            
            self._completed[job.job_id] = job
            
            # Update stats
            if success:
                self._stats["total_completed"] += 1
            else:
                self._stats["total_failed"] += 1
            
            self._stats["total_wait_time_s"] += job.wait_time_s
            self._stats["total_run_time_s"] += job.run_time_s
        
        # Release slot
        self._slot_available.release()
        
        # Call completion callback
        if job.on_complete:
            try:
                job.on_complete(job, success)
            except Exception as e:
                logger.warning(f"[Scheduler] Complete callback error: {e}")
        
        logger.debug(f"[Scheduler] Job {job.job_id} finished (success={success})")
    
    async def _aging_loop(self) -> None:
        """Periodically update job age bonuses for fair scheduling."""
        while self._running_flag:
            try:
                await asyncio.sleep(self.aging_interval_s)
                
                async with self._lock:
                    # Update age bonuses
                    for job in self._queue:
                        job.update_age_bonus(self.max_wait_for_priority_boost_s)
                    
                    # Re-heapify with updated priorities
                    heapq.heapify(self._queue)
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"[Scheduler] Aging loop error: {e}")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get scheduler statistics."""
        completed_count = self._stats["total_completed"] + self._stats["total_failed"]
        
        return {
            "running": len(self._running),
            "queued": len(self._queue),
            "max_concurrent": self.max_concurrent,
            "preemption_enabled": self.enable_preemption,
            "total_submitted": self._stats["total_submitted"],
            "total_completed": self._stats["total_completed"],
            "total_failed": self._stats["total_failed"],
            "total_cancelled": self._stats["total_cancelled"],
            "total_preempted": self._stats["total_preempted"],
            "avg_wait_time_s": (
                self._stats["total_wait_time_s"] / completed_count
                if completed_count > 0 else 0.0
            ),
            "avg_run_time_s": (
                self._stats["total_run_time_s"] / completed_count
                if completed_count > 0 else 0.0
            ),
            "success_rate": (
                self._stats["total_completed"] / completed_count
                if completed_count > 0 else 1.0
            ),
        }
    
    def get_queue_summary(self) -> List[Dict[str, Any]]:
        """Get summary of queued jobs."""
        return [
            {
                "job_id": job.job_id,
                "priority": job.priority.name,
                "wait_time_s": job.wait_time_s,
                "source": job.source[:50] if job.source else "",
                "user_id": job.user_id,
            }
            for job in sorted(self._queue)[:20]  # Top 20
        ]
    
    def get_running_summary(self) -> List[Dict[str, Any]]:
        """Get summary of running jobs."""
        return [
            {
                "job_id": job.job_id,
                "priority": job.priority.name,
                "run_time_s": job.run_time_s,
                "source": job.source[:50] if job.source else "",
                "user_id": job.user_id,
                "preemptable": job.preemptable,
            }
            for job in self._running.values()
        ]


# Global scheduler instance
_scheduler: Optional[JobScheduler] = None


def get_scheduler(
    max_concurrent: int = 4,
    enable_preemption: bool = False,
) -> JobScheduler:
    """Get the global scheduler instance."""
    global _scheduler
    if _scheduler is None:
        _scheduler = JobScheduler(
            max_concurrent=max_concurrent,
            enable_preemption=enable_preemption,
        )
    return _scheduler


async def init_scheduler(
    max_concurrent: int = 4,
    enable_preemption: bool = False,
) -> JobScheduler:
    """Initialize and start the global scheduler."""
    scheduler = get_scheduler(max_concurrent, enable_preemption)
    await scheduler.start()
    return scheduler


async def shutdown_scheduler() -> None:
    """Shutdown the global scheduler."""
    global _scheduler
    if _scheduler:
        await _scheduler.stop()
        _scheduler = None

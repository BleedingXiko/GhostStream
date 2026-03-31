"""
Global Job Scheduler for GhostStream — Specter-native (gevent).

Uses gevent primitives instead of asyncio for concurrency.
"""

import heapq
import logging
import time
import gevent
import gevent.lock
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import IntEnum
from typing import Optional, Dict, List, Callable, Any, Tuple, Set
from uuid import uuid4

logger = logging.getLogger(__name__)


class JobPriority(IntEnum):
    """Job priority levels (lower number = higher priority)."""
    CRITICAL = 1
    HIGH = 2
    NORMAL = 3
    LOW = 4
    IDLE = 5


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
    """A job in the scheduler queue."""
    sort_key: Tuple[int, float, float] = field(compare=True)
    job_id: str = field(compare=False)
    priority: JobPriority = field(compare=False, default=JobPriority.NORMAL)
    state: str = field(compare=False, default=JobState.PENDING)
    submit_time: datetime = field(compare=False, default_factory=datetime.utcnow)
    start_time: Optional[datetime] = field(compare=False, default=None)
    end_time: Optional[datetime] = field(compare=False, default=None)
    
    source: str = field(compare=False, default="")
    estimated_duration_s: float = field(compare=False, default=0.0)
    complexity_score: float = field(compare=False, default=1.0)
    user_id: Optional[str] = field(compare=False, default=None)
    
    retry_count: int = field(compare=False, default=0)
    max_retries: int = field(compare=False, default=3)
    preemptable: bool = field(compare=False, default=True)
    preemption_count: int = field(compare=False, default=0)
    
    on_start: Optional[Callable[["ScheduledJob"], Any]] = field(compare=False, default=None)
    on_complete: Optional[Callable[["ScheduledJob", bool], Any]] = field(compare=False, default=None)
    on_preempt: Optional[Callable[["ScheduledJob"], Any]] = field(compare=False, default=None)
    
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
        submit_time = datetime.utcnow()
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
        if self.start_time:
            return (self.start_time - self.submit_time).total_seconds()
        return (datetime.utcnow() - self.submit_time).total_seconds()
    
    @property
    def run_time_s(self) -> float:
        if not self.start_time:
            return 0.0
        end = self.end_time or datetime.utcnow()
        return (end - self.start_time).total_seconds()
    
    def update_age_bonus(self, max_wait_s: float = 300.0) -> None:
        wait_time = self.wait_time_s
        age_bonus = min(wait_time / max_wait_s, 1.0)
        self.sort_key = (int(self.priority), -age_bonus, self.submit_time.timestamp())


class JobScheduler:
    """Global job scheduler with priority queue and preemption (gevent-native)."""
    
    def __init__(
        self,
        max_concurrent: int = 4,
        enable_preemption: bool = False,
        max_queue_size: int = 1000,
        aging_interval_s: float = 10.0,
        max_wait_for_priority_boost_s: float = 300.0,
    ):
        self.max_concurrent = max_concurrent
        self.enable_preemption = enable_preemption
        self.max_queue_size = max_queue_size
        self.aging_interval_s = aging_interval_s
        self.max_wait_for_priority_boost_s = max_wait_for_priority_boost_s
        
        self._queue: List[ScheduledJob] = []
        self._running: Dict[str, ScheduledJob] = {}
        self._completed: Dict[str, ScheduledJob] = {}
        self._all_jobs: Dict[str, ScheduledJob] = {}
        
        self._lock = gevent.lock.BoundedSemaphore(1)
        self._slot_available = gevent.lock.BoundedSemaphore(max_concurrent)
        
        self._running_flag = False
        self._dispatch_greenlet: Optional[gevent.Greenlet] = None
        self._aging_greenlet: Optional[gevent.Greenlet] = None
        
        self._stats = {
            "total_submitted": 0,
            "total_completed": 0,
            "total_failed": 0,
            "total_cancelled": 0,
            "total_preempted": 0,
            "total_wait_time_s": 0.0,
            "total_run_time_s": 0.0,
        }
        
        self._job_executor: Optional[Callable[[ScheduledJob], Any]] = None
    
    def set_executor(self, executor: Callable[[ScheduledJob], Any]) -> None:
        self._job_executor = executor
    
    def start(self) -> None:
        if self._running_flag:
            return
        
        self._running_flag = True
        self._dispatch_greenlet = gevent.spawn(self._dispatch_loop)
        self._aging_greenlet = gevent.spawn(self._aging_loop)
        
        logger.info(f"[Scheduler] Started with max_concurrent={self.max_concurrent}, "
                   f"preemption={'enabled' if self.enable_preemption else 'disabled'}")
    
    def stop(self, timeout: float = 30.0) -> None:
        self._running_flag = False
        
        if self._dispatch_greenlet:
            self._dispatch_greenlet.kill(block=False)
        
        if self._aging_greenlet:
            self._aging_greenlet.kill(block=False)
        
        if self._running:
            logger.info(f"[Scheduler] Waiting for {len(self._running)} running jobs...")
            start = time.time()
            while self._running and (time.time() - start) < timeout:
                gevent.sleep(0.5)
        
        with self._lock:
            for job in self._running.values():
                job.state = JobState.CANCELLED
                job.end_time = datetime.utcnow()
            self._running.clear()
            
            for job in self._queue:
                job.state = JobState.CANCELLED
            self._queue.clear()
        
        logger.info("[Scheduler] Stopped")
    
    def submit(
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
        if not self._running_flag:
            raise RuntimeError("Scheduler is not running")
        
        job_id = job_id or str(uuid4())
        
        with self._lock:
            if len(self._queue) >= self.max_queue_size:
                return False, "Queue is full", None
            
            if job_id in self._all_jobs:
                return False, f"Job {job_id} already exists", self._all_jobs[job_id]
            
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
            
            heapq.heappush(self._queue, job)
            self._all_jobs[job_id] = job
            self._stats["total_submitted"] += 1
            
            queue_pos = len(self._queue)
        
        logger.info(f"[Scheduler] Job {job_id} submitted (priority={priority.name}, pos={queue_pos})")
        return True, f"Queued at position {queue_pos}", job
    
    def cancel(self, job_id: str) -> Tuple[bool, str]:
        with self._lock:
            job = self._all_jobs.get(job_id)
            if not job:
                return False, "Job not found"
            
            if job.state in (JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED):
                return False, f"Job already in terminal state: {job.state}"
            
            if job.state == JobState.RUNNING:
                job.state = JobState.CANCELLED
                job.end_time = datetime.utcnow()
                if job_id in self._running:
                    del self._running[job_id]
                self._slot_available.release()
                self._stats["total_cancelled"] += 1
                return True, "Cancelled running job"
            
            if job.state == JobState.QUEUED:
                job.state = JobState.CANCELLED
                self._queue = [j for j in self._queue if j.job_id != job_id]
                heapq.heapify(self._queue)
                self._stats["total_cancelled"] += 1
                return True, "Cancelled queued job"
            
            return False, f"Cannot cancel job in state: {job.state}"
    
    def get_job(self, job_id: str) -> Optional[ScheduledJob]:
        with self._lock:
            return self._all_jobs.get(job_id)
    
    def get_queue_position(self, job_id: str) -> int:
        with self._lock:
            job = self._all_jobs.get(job_id)
            if not job:
                return -1
            if job.state == JobState.RUNNING:
                return 0
            if job.state != JobState.QUEUED:
                return -1
            
            for i, j in enumerate(sorted(self._queue)):
                if j.job_id == job_id:
                    return i + 1
            return -1
    
    def _dispatch_loop(self) -> None:
        while self._running_flag:
            try:
                self._slot_available.acquire()
                
                job = self._get_next_job()
                if not job:
                    self._slot_available.release()
                    gevent.sleep(0.1)
                    continue
                
                if self.enable_preemption and job.priority <= JobPriority.HIGH:
                    preempted = self._try_preempt(job)
                    if preempted:
                        self._slot_available.release()
                
                gevent.spawn(self._execute_job, job)
                
            except gevent.GreenletExit:
                break
            except Exception as e:
                logger.error(f"[Scheduler] Dispatch error: {e}")
                self._slot_available.release()
                gevent.sleep(1.0)
    
    def _get_next_job(self) -> Optional[ScheduledJob]:
        with self._lock:
            while self._queue:
                job = heapq.heappop(self._queue)
                if job.state == JobState.QUEUED:
                    return job
            return None
    
    def _try_preempt(self, high_priority_job: ScheduledJob) -> bool:
        with self._lock:
            candidates = [
                (job_id, job) for job_id, job in self._running.items()
                if job.preemptable and job.priority > high_priority_job.priority
            ]
            
            if not candidates:
                return False
            
            candidates.sort(key=lambda x: (-x[1].priority.value, x[1].run_time_s))
            victim_id, victim = candidates[0]
            
            victim.state = JobState.PREEMPTED
            victim.preemption_count += 1
            del self._running[victim_id]
            
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
    
    def _execute_job(self, job: ScheduledJob) -> None:
        job.state = JobState.RUNNING
        job.start_time = datetime.utcnow()
        
        with self._lock:
            self._running[job.job_id] = job
        
        if job.on_start:
            try:
                job.on_start(job)
            except Exception as e:
                logger.warning(f"[Scheduler] Start callback error: {e}")
        
        logger.debug(f"[Scheduler] Starting job {job.job_id}")
        
        success = False
        try:
            if self._job_executor:
                job.result = self._job_executor(job)
                success = True
            else:
                job.error = "No executor configured"
                
        except gevent.GreenletExit:
            job.state = JobState.CANCELLED
            job.error = "Cancelled"
        except Exception as e:
            job.state = JobState.FAILED
            job.error = str(e)
            logger.error(f"[Scheduler] Job {job.job_id} failed: {e}")
        
        job.end_time = datetime.utcnow()
        
        with self._lock:
            if job.job_id in self._running:
                del self._running[job.job_id]
            
            if job.state == JobState.RUNNING:
                job.state = JobState.COMPLETED if success else JobState.FAILED
            
            self._completed[job.job_id] = job
            
            if success:
                self._stats["total_completed"] += 1
            else:
                self._stats["total_failed"] += 1
            
            self._stats["total_wait_time_s"] += job.wait_time_s
            self._stats["total_run_time_s"] += job.run_time_s
        
        self._slot_available.release()
        
        if job.on_complete:
            try:
                job.on_complete(job, success)
            except Exception as e:
                logger.warning(f"[Scheduler] Complete callback error: {e}")
        
        logger.debug(f"[Scheduler] Job {job.job_id} finished (success={success})")
    
    def _aging_loop(self) -> None:
        while self._running_flag:
            try:
                gevent.sleep(self.aging_interval_s)
                
                with self._lock:
                    for job in self._queue:
                        job.update_age_bonus(self.max_wait_for_priority_boost_s)
                    
                    heapq.heapify(self._queue)
                    
            except gevent.GreenletExit:
                break
            except Exception as e:
                logger.debug(f"[Scheduler] Aging loop error: {e}")
    
    def get_stats(self) -> Dict[str, Any]:
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
        return [
            {
                "job_id": job.job_id,
                "priority": job.priority.name,
                "wait_time_s": job.wait_time_s,
                "source": job.source[:50] if job.source else "",
                "user_id": job.user_id,
            }
            for job in sorted(self._queue)[:20]
        ]
    
    def get_running_summary(self) -> List[Dict[str, Any]]:
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
    global _scheduler
    if _scheduler is None:
        _scheduler = JobScheduler(
            max_concurrent=max_concurrent,
            enable_preemption=enable_preemption,
        )
    return _scheduler


def init_scheduler(
    max_concurrent: int = 4,
    enable_preemption: bool = False,
) -> JobScheduler:
    scheduler = get_scheduler(max_concurrent, enable_preemption)
    scheduler.start()
    return scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.stop()
        _scheduler = None

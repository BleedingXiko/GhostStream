"""
Global FFmpeg worker wrapper for GhostStream.

Provides a centralized process management layer for FFmpeg operations with:
- Process lifecycle management
- Resource tracking
- Graceful shutdown handling
- Cross-platform signal support
"""

import asyncio
import logging
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, List, Callable, Any, Tuple
from pathlib import Path

logger = logging.getLogger(__name__)


class WorkerState(str, Enum):
    """FFmpeg worker process state."""
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass
class WorkerStats:
    """Statistics for a worker process."""
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    bytes_processed: int = 0
    frames_processed: int = 0
    last_progress_time: Optional[datetime] = None
    return_code: Optional[int] = None
    error_message: str = ""
    
    @property
    def duration_seconds(self) -> float:
        """Get worker run duration in seconds."""
        if not self.start_time:
            return 0.0
        end = self.end_time or datetime.utcnow()
        return (end - self.start_time).total_seconds()


@dataclass
class FFmpegWorker:
    """
    Wrapper around a single FFmpeg process.
    
    Manages the lifecycle of an FFmpeg process including startup,
    progress monitoring, and graceful shutdown.
    """
    worker_id: str
    command: List[str]
    working_dir: Optional[Path] = None
    state: WorkerState = WorkerState.IDLE
    process: Optional[asyncio.subprocess.Process] = None
    stats: WorkerStats = field(default_factory=WorkerStats)
    _cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    _stdout_buffer: List[bytes] = field(default_factory=list)
    _stderr_buffer: List[str] = field(default_factory=list)
    
    async def start(self) -> bool:
        """Start the FFmpeg process."""
        if self.state != WorkerState.IDLE:
            logger.warning(f"[Worker {self.worker_id}] Cannot start - state is {self.state}")
            return False
        
        self.state = WorkerState.STARTING
        self.stats.start_time = datetime.utcnow()
        
        try:
            kwargs: Dict[str, Any] = {
                "stdout": asyncio.subprocess.PIPE,
                "stderr": asyncio.subprocess.PIPE,
            }
            
            if self.working_dir:
                kwargs["cwd"] = str(self.working_dir)
            
            # Windows-specific process group for signal handling
            if sys.platform == "win32":
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            
            self.process = await asyncio.create_subprocess_exec(
                *self.command, **kwargs
            )
            
            self.state = WorkerState.RUNNING
            logger.info(f"[Worker {self.worker_id}] Started FFmpeg process (PID: {self.process.pid})")
            return True
            
        except Exception as e:
            self.state = WorkerState.ERROR
            self.stats.error_message = str(e)
            logger.error(f"[Worker {self.worker_id}] Failed to start: {e}")
            return False
    
    async def stop(self, timeout: float = 10.0) -> int:
        """
        Stop the FFmpeg process gracefully.
        
        Args:
            timeout: Maximum time to wait for graceful shutdown
            
        Returns:
            Process return code (-1 if failed to stop)
        """
        if self.process is None or self.process.returncode is not None:
            return self.process.returncode if self.process else -1
        
        self.state = WorkerState.STOPPING
        self._cancel_event.set()
        
        try:
            # Try graceful shutdown first
            if sys.platform == "win32":
                try:
                    self.process.send_signal(signal.CTRL_BREAK_EVENT)
                except (ProcessLookupError, OSError):
                    pass
            else:
                try:
                    self.process.send_signal(signal.SIGINT)
                except (ProcessLookupError, OSError):
                    pass
            
            # Wait for graceful shutdown
            try:
                await asyncio.wait_for(self.process.wait(), timeout=timeout * 0.5)
                self.state = WorkerState.STOPPED
                self.stats.end_time = datetime.utcnow()
                self.stats.return_code = self.process.returncode
                return self.process.returncode
            except asyncio.TimeoutError:
                pass
            
            # Escalate to terminate
            try:
                self.process.terminate()
                await asyncio.wait_for(self.process.wait(), timeout=timeout * 0.3)
                self.state = WorkerState.STOPPED
                self.stats.end_time = datetime.utcnow()
                self.stats.return_code = self.process.returncode
                return self.process.returncode
            except (asyncio.TimeoutError, ProcessLookupError, OSError):
                pass
            
            # Force kill
            try:
                self.process.kill()
                await self.process.wait()
            except (ProcessLookupError, OSError):
                pass
            
            self.state = WorkerState.STOPPED
            self.stats.end_time = datetime.utcnow()
            self.stats.return_code = self.process.returncode if self.process.returncode is not None else -1
            
            logger.warning(f"[Worker {self.worker_id}] Force killed")
            return self.stats.return_code
            
        except Exception as e:
            logger.error(f"[Worker {self.worker_id}] Error stopping: {e}")
            self.state = WorkerState.ERROR
            self.stats.error_message = str(e)
            return -1
    
    async def wait(self) -> int:
        """Wait for the process to complete."""
        if self.process is None:
            return -1
        
        await self.process.wait()
        self.state = WorkerState.STOPPED
        self.stats.end_time = datetime.utcnow()
        self.stats.return_code = self.process.returncode
        return self.process.returncode if self.process.returncode is not None else -1
    
    def get_stderr(self) -> str:
        """Get accumulated stderr output."""
        return "".join(self._stderr_buffer)
    
    def is_running(self) -> bool:
        """Check if the worker is currently running."""
        return self.state == WorkerState.RUNNING and self.process is not None and self.process.returncode is None


class FFmpegWorkerPool:
    """
    Pool of FFmpeg worker processes.
    
    Manages multiple FFmpeg processes with:
    - Concurrency limiting
    - Resource tracking
    - Centralized shutdown
    """
    
    def __init__(self, max_workers: int = 4):
        self.max_workers = max_workers
        self._workers: Dict[str, FFmpegWorker] = {}
        self._semaphore = asyncio.Semaphore(max_workers)
        self._lock = asyncio.Lock()
        self._running = False
        
    async def start(self) -> None:
        """Start the worker pool."""
        self._running = True
        logger.info(f"[WorkerPool] Started with max {self.max_workers} workers")
    
    async def stop(self) -> None:
        """Stop all workers and the pool."""
        self._running = False
        
        # Stop all active workers
        async with self._lock:
            stop_tasks = []
            for worker_id, worker in self._workers.items():
                if worker.is_running():
                    stop_tasks.append(worker.stop())
            
            if stop_tasks:
                await asyncio.gather(*stop_tasks, return_exceptions=True)
        
        logger.info(f"[WorkerPool] Stopped, {len(self._workers)} workers cleaned up")
    
    async def create_worker(
        self,
        worker_id: str,
        command: List[str],
        working_dir: Optional[Path] = None
    ) -> Optional[FFmpegWorker]:
        """
        Create and register a new worker.
        
        Args:
            worker_id: Unique identifier for the worker
            command: FFmpeg command to execute
            working_dir: Working directory for the process
            
        Returns:
            FFmpegWorker instance or None if pool is full
        """
        if not self._running:
            logger.warning("[WorkerPool] Cannot create worker - pool is stopped")
            return None
        
        async with self._lock:
            if worker_id in self._workers:
                logger.warning(f"[WorkerPool] Worker {worker_id} already exists")
                return self._workers[worker_id]
            
            worker = FFmpegWorker(
                worker_id=worker_id,
                command=command,
                working_dir=working_dir
            )
            self._workers[worker_id] = worker
            return worker
    
    async def acquire_slot(self, timeout: Optional[float] = None) -> bool:
        """
        Acquire a worker slot from the pool.
        
        Args:
            timeout: Maximum time to wait for a slot
            
        Returns:
            True if slot acquired, False if timed out
        """
        try:
            if timeout:
                await asyncio.wait_for(self._semaphore.acquire(), timeout=timeout)
            else:
                await self._semaphore.acquire()
            return True
        except asyncio.TimeoutError:
            return False
    
    def release_slot(self) -> None:
        """Release a worker slot back to the pool."""
        self._semaphore.release()
    
    async def run_worker(
        self,
        worker_id: str,
        command: List[str],
        working_dir: Optional[Path] = None,
        progress_callback: Optional[Callable[[str, int, float], None]] = None,
        timeout: Optional[float] = None
    ) -> Tuple[int, str]:
        """
        Run an FFmpeg command using a pooled worker.
        
        Args:
            worker_id: Unique identifier for this job
            command: FFmpeg command to execute
            working_dir: Working directory
            progress_callback: Called with (worker_id, frame, time) on progress
            timeout: Maximum execution time
            
        Returns:
            Tuple of (return_code, stderr_output)
        """
        # Acquire pool slot
        if not await self.acquire_slot(timeout=30.0):
            return -1, "Failed to acquire worker slot"
        
        try:
            worker = await self.create_worker(worker_id, command, working_dir)
            if not worker:
                return -1, "Failed to create worker"
            
            if not await worker.start():
                return -1, worker.stats.error_message
            
            # Read output concurrently
            async def read_stderr():
                while worker.is_running():
                    try:
                        line = await asyncio.wait_for(
                            worker.process.stderr.readline(),
                            timeout=1.0
                        )
                        if not line:
                            break
                        
                        line_str = line.decode("utf-8", errors="ignore")
                        worker._stderr_buffer.append(line_str)
                        
                        # Keep buffer bounded
                        if len(worker._stderr_buffer) > 200:
                            worker._stderr_buffer.pop(0)
                        
                        # Parse progress if callback provided
                        if progress_callback and "frame=" in line_str:
                            worker.stats.last_progress_time = datetime.utcnow()
                            # Extract frame number
                            import re
                            match = re.search(r"frame=\s*(\d+)", line_str)
                            if match:
                                worker.stats.frames_processed = int(match.group(1))
                            match = re.search(r"time=\s*(\d+):(\d+):(\d+\.?\d*)", line_str)
                            if match:
                                h, m, s = match.groups()
                                time_val = int(h) * 3600 + int(m) * 60 + float(s)
                                progress_callback(
                                    worker_id,
                                    worker.stats.frames_processed,
                                    time_val
                                )
                    except asyncio.TimeoutError:
                        continue
                    except Exception as e:
                        logger.debug(f"[Worker {worker_id}] stderr read error: {e}")
                        break
            
            async def read_stdout():
                while worker.is_running():
                    try:
                        chunk = await worker.process.stdout.read(4096)
                        if not chunk:
                            break
                        worker._stdout_buffer.append(chunk)
                    except Exception:
                        break
            
            # Run readers and wait for completion
            stderr_task = asyncio.create_task(read_stderr())
            stdout_task = asyncio.create_task(read_stdout())
            
            try:
                if timeout:
                    return_code = await asyncio.wait_for(worker.wait(), timeout=timeout)
                else:
                    return_code = await worker.wait()
            except asyncio.TimeoutError:
                logger.warning(f"[Worker {worker_id}] Timed out after {timeout}s")
                await worker.stop()
                return_code = -1
            
            # Wait for readers to finish
            await asyncio.gather(stderr_task, stdout_task, return_exceptions=True)
            
            return return_code, worker.get_stderr()
            
        finally:
            # Clean up worker
            async with self._lock:
                if worker_id in self._workers:
                    del self._workers[worker_id]
            
            self.release_slot()
    
    def get_active_count(self) -> int:
        """Get number of active workers."""
        return sum(1 for w in self._workers.values() if w.is_running())
    
    def get_stats(self) -> Dict[str, Any]:
        """Get pool statistics."""
        return {
            "max_workers": self.max_workers,
            "active_workers": self.get_active_count(),
            "total_workers": len(self._workers),
            "available_slots": self.max_workers - self.get_active_count(),
            "running": self._running,
        }


# Global worker pool instance
_worker_pool: Optional[FFmpegWorkerPool] = None


def get_worker_pool(max_workers: int = 4) -> FFmpegWorkerPool:
    """Get the global worker pool instance."""
    global _worker_pool
    if _worker_pool is None:
        _worker_pool = FFmpegWorkerPool(max_workers)
    return _worker_pool


async def init_worker_pool(max_workers: int = 4) -> FFmpegWorkerPool:
    """Initialize and start the global worker pool."""
    pool = get_worker_pool(max_workers)
    await pool.start()
    return pool


async def shutdown_worker_pool() -> None:
    """Shutdown the global worker pool."""
    global _worker_pool
    if _worker_pool:
        await _worker_pool.stop()
        _worker_pool = None

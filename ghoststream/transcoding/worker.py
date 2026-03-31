"""
Global FFmpeg worker wrapper for GhostStream — Specter-native (gevent).

Uses subprocess.Popen + gevent primitives instead of asyncio.
"""

import logging
import signal
import subprocess
import sys
import time
import gevent
import gevent.event
import gevent.lock
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
        if not self.start_time:
            return 0.0
        end = self.end_time or datetime.utcnow()
        return (end - self.start_time).total_seconds()


@dataclass
class FFmpegWorker:
    """Wrapper around a single FFmpeg process (gevent-native)."""
    worker_id: str
    command: List[str]
    working_dir: Optional[Path] = None
    state: WorkerState = WorkerState.IDLE
    process: Optional[subprocess.Popen] = None
    stats: WorkerStats = field(default_factory=WorkerStats)
    _cancel_event: gevent.event.Event = field(default_factory=gevent.event.Event)
    _stdout_buffer: List[bytes] = field(default_factory=list)
    _stderr_buffer: List[str] = field(default_factory=list)
    
    def start(self) -> bool:
        """Start the FFmpeg process."""
        if self.state != WorkerState.IDLE:
            logger.warning(f"[Worker {self.worker_id}] Cannot start - state is {self.state}")
            return False
        
        self.state = WorkerState.STARTING
        self.stats.start_time = datetime.utcnow()
        
        try:
            kwargs: Dict[str, Any] = {
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
            }
            
            if self.working_dir:
                kwargs["cwd"] = str(self.working_dir)
            
            if sys.platform == "win32":
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            
            self.process = subprocess.Popen(self.command, **kwargs)
            
            self.state = WorkerState.RUNNING
            logger.info(f"[Worker {self.worker_id}] Started FFmpeg process (PID: {self.process.pid})")
            return True
            
        except Exception as e:
            self.state = WorkerState.ERROR
            self.stats.error_message = str(e)
            logger.error(f"[Worker {self.worker_id}] Failed to start: {e}")
            return False
    
    def stop(self, timeout: float = 10.0) -> int:
        """Stop the FFmpeg process gracefully."""
        if self.process is None or self.process.poll() is not None:
            return self.process.returncode if self.process else -1
        
        self.state = WorkerState.STOPPING
        self._cancel_event.set()
        
        try:
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
            
            try:
                gevent.with_timeout(timeout * 0.5, self.process.wait)
                self.state = WorkerState.STOPPED
                self.stats.end_time = datetime.utcnow()
                self.stats.return_code = self.process.returncode
                return self.process.returncode
            except gevent.Timeout:
                pass
            
            try:
                self.process.terminate()
                gevent.with_timeout(timeout * 0.3, self.process.wait)
                self.state = WorkerState.STOPPED
                self.stats.end_time = datetime.utcnow()
                self.stats.return_code = self.process.returncode
                return self.process.returncode
            except (gevent.Timeout, ProcessLookupError, OSError):
                pass
            
            try:
                self.process.kill()
                self.process.wait()
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
    
    def wait(self) -> int:
        """Wait for the process to complete."""
        if self.process is None:
            return -1
        
        self.process.wait()
        self.state = WorkerState.STOPPED
        self.stats.end_time = datetime.utcnow()
        self.stats.return_code = self.process.returncode
        return self.process.returncode if self.process.returncode is not None else -1
    
    def get_stderr(self) -> str:
        return "".join(self._stderr_buffer)
    
    def is_running(self) -> bool:
        return self.state == WorkerState.RUNNING and self.process is not None and self.process.poll() is None


class FFmpegWorkerPool:
    """Pool of FFmpeg worker processes (gevent-native)."""
    
    def __init__(self, max_workers: int = 4):
        self.max_workers = max_workers
        self._workers: Dict[str, FFmpegWorker] = {}
        self._semaphore = gevent.lock.BoundedSemaphore(max_workers)
        self._lock = gevent.lock.BoundedSemaphore(1)
        self._running = False
        
    def start(self) -> None:
        self._running = True
        logger.info(f"[WorkerPool] Started with max {self.max_workers} workers")
    
    def stop(self) -> None:
        self._running = False
        
        with self._lock:
            stop_greenlets = []
            for worker_id, worker in self._workers.items():
                if worker.is_running():
                    stop_greenlets.append(gevent.spawn(worker.stop))
            
            if stop_greenlets:
                gevent.joinall(stop_greenlets, timeout=30)
        
        logger.info(f"[WorkerPool] Stopped, {len(self._workers)} workers cleaned up")
    
    def create_worker(
        self,
        worker_id: str,
        command: List[str],
        working_dir: Optional[Path] = None
    ) -> Optional[FFmpegWorker]:
        if not self._running:
            logger.warning("[WorkerPool] Cannot create worker - pool is stopped")
            return None
        
        with self._lock:
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
    
    def acquire_slot(self, timeout: Optional[float] = None) -> bool:
        try:
            if timeout:
                return gevent.with_timeout(timeout, self._semaphore.acquire)
            else:
                self._semaphore.acquire()
                return True
        except gevent.Timeout:
            return False
    
    def release_slot(self) -> None:
        self._semaphore.release()
    
    def run_worker(
        self,
        worker_id: str,
        command: List[str],
        working_dir: Optional[Path] = None,
        progress_callback: Optional[Callable[[str, int, float], None]] = None,
        timeout: Optional[float] = None
    ) -> Tuple[int, str]:
        if not self.acquire_slot(timeout=30.0):
            return -1, "Failed to acquire worker slot"
        
        try:
            worker = self.create_worker(worker_id, command, working_dir)
            if not worker:
                return -1, "Failed to create worker"
            
            if not worker.start():
                return -1, worker.stats.error_message
            
            def read_stderr():
                import re as re_mod
                while worker.is_running():
                    try:
                        line = worker.process.stderr.readline()
                        if not line:
                            break
                        
                        line_str = line.decode("utf-8", errors="ignore")
                        worker._stderr_buffer.append(line_str)
                        
                        if len(worker._stderr_buffer) > 200:
                            worker._stderr_buffer.pop(0)
                        
                        if progress_callback and "frame=" in line_str:
                            worker.stats.last_progress_time = datetime.utcnow()
                            match = re_mod.search(r"frame=\s*(\d+)", line_str)
                            if match:
                                worker.stats.frames_processed = int(match.group(1))
                            match = re_mod.search(r"time=\s*(\d+):(\d+):(\d+\.?\d*)", line_str)
                            if match:
                                h, m, s = match.groups()
                                time_val = int(h) * 3600 + int(m) * 60 + float(s)
                                progress_callback(
                                    worker_id,
                                    worker.stats.frames_processed,
                                    time_val
                                )
                    except Exception as e:
                        logger.debug(f"[Worker {worker_id}] stderr read error: {e}")
                        break
            
            def read_stdout():
                while worker.is_running():
                    try:
                        chunk = worker.process.stdout.read(4096)
                        if not chunk:
                            break
                        worker._stdout_buffer.append(chunk)
                    except Exception:
                        break
            
            stderr_glet = gevent.spawn(read_stderr)
            stdout_glet = gevent.spawn(read_stdout)
            
            try:
                if timeout:
                    return_code = gevent.with_timeout(timeout, worker.wait)
                else:
                    return_code = worker.wait()
            except gevent.Timeout:
                logger.warning(f"[Worker {worker_id}] Timed out after {timeout}s")
                worker.stop()
                return_code = -1
            
            gevent.joinall([stderr_glet, stdout_glet], timeout=5)
            
            return return_code, worker.get_stderr()
            
        finally:
            with self._lock:
                if worker_id in self._workers:
                    del self._workers[worker_id]
            
            self.release_slot()
    
    def get_active_count(self) -> int:
        return sum(1 for w in self._workers.values() if w.is_running())
    
    def get_stats(self) -> Dict[str, Any]:
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
    global _worker_pool
    if _worker_pool is None:
        _worker_pool = FFmpegWorkerPool(max_workers)
    return _worker_pool


def init_worker_pool(max_workers: int = 4) -> FFmpegWorkerPool:
    pool = get_worker_pool(max_workers)
    pool.start()
    return pool


def shutdown_worker_pool() -> None:
    global _worker_pool
    if _worker_pool:
        _worker_pool.stop()
        _worker_pool = None

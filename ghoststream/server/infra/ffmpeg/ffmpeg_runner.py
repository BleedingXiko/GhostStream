"""
FFmpeg process execution with progress tracking and stall detection — Specter-native (gevent).

Uses subprocess.Popen + gevent greenlets instead of asyncio tasks.
"""

import os
import re
import signal
import subprocess
import sys
import time
import logging
import gevent
import gevent.event
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Callable, List, Dict, Any, Tuple

from .job_context import JobContext
from ...domain.transcoding.constants import STDERR_BUFFER_SIZE, STDERR_EARLY_BUFFER_SIZE
from ...domain.transcoding.models import MediaInfo, TranscodeProgress

logger = logging.getLogger(__name__)


@dataclass
class StallConfig:
    """Configuration for stall detection."""
    base_timeout: float = 120.0
    timeout_per_segment: float = 10.0
    grace_period: float = 30.0
    resolution_factor_4k: float = 2.0
    resolution_factor_1080p: float = 1.5
    hdr_grace_bonus: float = 15.0


class ProgressParser:
    """
    Centralized FFmpeg progress parsing with throttling.
    """
    
    _RE_FRAME = re.compile(r"frame=\s*(\d+)")
    _RE_FPS = re.compile(r"fps=\s*([\d.]+|N/A)")
    _RE_BITRATE = re.compile(r"bitrate=\s*([\d.]+\s*[kMG]?bits/s|N/A)")
    _RE_SIZE = re.compile(r"size=\s*(\d+)\s*(kB|MB|B)?")
    _RE_TIME_FULL = re.compile(r"time=\s*(\d+):(\d+):(\d+\.?\d*)")
    _RE_TIME_SHORT = re.compile(r"time=\s*(\d+):(\d+\.?\d*)")
    _RE_SPEED = re.compile(r"speed=\s*([\d.]+)x")
    _RE_QUALITY = re.compile(r"q=\s*([\d.-]+)")
    
    def __init__(self, throttle_interval: float = 0.5):
        self.throttle_interval = throttle_interval
        self._last_callback_time = 0.0
        self._pending_line: Optional[str] = None
    
    def should_parse(self, line: str) -> bool:
        """Check if line contains progress info."""
        return "frame=" in line or "size=" in line or "time=" in line
    
    def parse(self, line: str, progress: TranscodeProgress, 
              media_info: MediaInfo) -> bool:
        """Parse FFmpeg progress line and update progress object."""
        found_progress = False
        
        match = self._RE_FRAME.search(line)
        if match:
            try:
                progress.frame = int(match.group(1))
                found_progress = True
            except (ValueError, TypeError):
                pass
        
        match = self._RE_FPS.search(line)
        if match and match.group(1) != "N/A":
            try:
                progress.fps = float(match.group(1))
            except (ValueError, TypeError):
                pass
        
        match = self._RE_BITRATE.search(line)
        if match and match.group(1) != "N/A":
            progress.bitrate = match.group(1).strip()
        
        match = self._RE_SIZE.search(line)
        if match:
            try:
                size_val = int(match.group(1))
                unit = match.group(2) or "kB"
                if unit == "MB":
                    progress.total_size = size_val * 1024 * 1024
                elif unit == "kB":
                    progress.total_size = size_val * 1024
                else:
                    progress.total_size = size_val
                found_progress = True
            except (ValueError, TypeError):
                pass
        
        match = self._RE_TIME_FULL.search(line)
        if match:
            try:
                h, m, s = match.groups()
                progress.time = int(h) * 3600 + int(m) * 60 + float(s)
                found_progress = True
            except (ValueError, TypeError):
                pass
        else:
            match = self._RE_TIME_SHORT.search(line)
            if match:
                try:
                    m, s = match.groups()
                    progress.time = int(m) * 60 + float(s)
                    found_progress = True
                except (ValueError, TypeError):
                    pass
        
        match = self._RE_SPEED.search(line)
        if match:
            try:
                progress.speed = float(match.group(1))
            except (ValueError, TypeError):
                pass
        
        if media_info.duration > 0 and progress.time > 0:
            progress.percent = min(99.9, (progress.time / media_info.duration) * 100)
        
        return found_progress
    
    def should_callback(self) -> bool:
        """Check if enough time has passed to fire callback."""
        now = time.time()
        if now - self._last_callback_time >= self.throttle_interval:
            self._last_callback_time = now
            return True
        return False


class FFmpegRunner:
    """
    Executes FFmpeg processes with progress tracking and stall detection.
    
    Specter-native: uses subprocess.Popen + gevent greenlets.
    """
    
    def __init__(
        self,
        stall_config: Optional[StallConfig] = None,
        verbose: bool = False
    ):
        self.stall_config = stall_config or StallConfig()
        self.verbose = verbose or os.environ.get('GHOSTSTREAM_FFMPEG_VERBOSE', '').lower() in ('1', 'true', 'yes')
    
    def calculate_stall_timeout(self, media_info: MediaInfo, segment_duration: int = 4) -> float:
        """Calculate dynamic stall timeout based on content."""
        cfg = self.stall_config
        base_timeout = cfg.base_timeout
        
        segment_factor = cfg.timeout_per_segment * segment_duration
        
        resolution_factor = 1.0
        if media_info.width >= 3840:
            resolution_factor = cfg.resolution_factor_4k
        elif media_info.width >= 1920:
            resolution_factor = cfg.resolution_factor_1080p
        
        timeout = base_timeout + (segment_factor * resolution_factor)
        
        logger.debug(f"[FFmpegRunner] Stall timeout: {timeout:.0f}s "
                    f"(base={base_timeout}, segment={segment_duration}s, res_factor={resolution_factor})")
        
        return timeout
    
    def get_grace_period(self, media_info: MediaInfo) -> float:
        """Get grace period before stall detection begins."""
        cfg = self.stall_config
        grace = cfg.grace_period
        
        if media_info.width >= 3840:
            grace += 30.0
        elif media_info.width >= 1920:
            grace += 15.0
        
        if media_info.is_hdr:
            grace += cfg.hdr_grace_bonus
        
        return grace
    
    def run(
        self,
        cmd: List[str],
        media_info: MediaInfo,
        progress_callback: Optional[Callable[[TranscodeProgress], None]] = None,
        cancel_event: Optional[gevent.event.Event] = None,
        stage: str = "transcoding",
        job_context: Optional[JobContext] = None,
        segment_duration: int = 4
    ) -> Tuple[int, str]:
        """
        Run FFmpeg process with progress tracking.
        
        Returns:
            Tuple of (return_code, error_output).
        """
        log_prefix = job_context.log_prefix if job_context else "[FFmpeg]"
        logger.info(f"{log_prefix} Running: {' '.join(cmd[:10])}...")
        
        stall_timeout = self.calculate_stall_timeout(media_info, segment_duration)
        grace_period = self.get_grace_period(media_info)
        
        process = self._spawn_process(cmd, log_prefix)
        if process is None:
            return -1, "Failed to start FFmpeg process"
        
        progress = TranscodeProgress(stage=stage)
        progress_parser = ProgressParser(throttle_interval=0.5)
        state = {
            "stderr_lines": [],
            "stderr_early": [],
            "stdout_bytes": 0,
            "last_progress_time": time.time(),
            "last_file_size": 0,
            "stalled": False,
            "cancelled": False,
            "start_time": time.time(),
        }
        job_dir = job_context.job_dir if job_context else None
        
        stdout_glet = gevent.spawn(
            self._read_stdout, process, state, log_prefix
        )
        stderr_glet = gevent.spawn(
            self._read_stderr, process, state, progress, progress_parser,
            media_info, progress_callback, log_prefix
        )
        monitor_glet = gevent.spawn(
            self._monitor_stall_and_cancel,
            process, state, stall_timeout, grace_period,
            cancel_event, job_dir, log_prefix
        )
        
        gevent.joinall([stdout_glet, stderr_glet, monitor_glet], raise_error=False)
        
        # Ensure process has terminated
        try:
            gevent.with_timeout(10.0, process.wait)
        except gevent.Timeout:
            logger.error(f"{log_prefix} FFmpeg did not exit, force killing")
            self._graceful_terminate(process)
        
        return_code = process.returncode if process.returncode is not None else -1
        
        error_output = "".join(state["stderr_lines"])
        if state["stalled"]:
            error_output = f"[STALLED after {stall_timeout:.0f}s] " + error_output
        if state["cancelled"]:
            error_output = "[CANCELLED] " + error_output
        
        return return_code, error_output
    
    def _spawn_process(
        self,
        cmd: List[str],
        log_prefix: str
    ) -> Optional[subprocess.Popen]:
        """Spawn FFmpeg subprocess."""
        try:
            kwargs: Dict[str, Any] = {
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
            }
            if sys.platform == "win32":
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            
            return subprocess.Popen(cmd, **kwargs)
        except Exception as e:
            logger.error(f"{log_prefix} Failed to start FFmpeg: {e}")
            return None
    
    def _read_stdout(
        self,
        process: subprocess.Popen,
        state: Dict[str, Any],
        log_prefix: str
    ) -> None:
        """Read stdout to prevent pipe blocking."""
        try:
            while True:
                chunk = process.stdout.read(4096)
                if not chunk:
                    break
                state["stdout_bytes"] += len(chunk)
                
                if self.verbose:
                    logger.debug(f"{log_prefix} stdout: {chunk.decode('utf-8', errors='ignore')[:200]}")
        except Exception as e:
            logger.debug(f"{log_prefix} stdout reader error: {e}")
    
    def _read_stderr(
        self,
        process: subprocess.Popen,
        state: Dict[str, Any],
        progress: TranscodeProgress,
        parser: ProgressParser,
        media_info: MediaInfo,
        progress_callback: Optional[Callable[[TranscodeProgress], None]],
        log_prefix: str
    ) -> None:
        """Read stderr and parse progress with throttled callbacks."""
        try:
            while True:
                line = process.stderr.readline()
                if not line:
                    break
                
                line_str = line.decode("utf-8", errors="ignore")
                
                if len(state["stderr_early"]) < STDERR_EARLY_BUFFER_SIZE:
                    state["stderr_early"].append(line_str)
                
                state["stderr_lines"].append(line_str)
                if len(state["stderr_lines"]) > STDERR_BUFFER_SIZE:
                    state["stderr_lines"].pop(0)
                
                if parser.should_parse(line_str):
                    state["last_progress_time"] = time.time()
                    parser.parse(line_str, progress, media_info)
                    
                    # Log progress at INFO level only if verbose is explicitly enabled
                    if self.verbose:
                        logger.info(f"{log_prefix} [FFmpeg] {line_str.strip()}")
                    else:
                        logger.debug(f"{log_prefix} [FFmpeg] {line_str.strip()}")
                    
                    if progress_callback and parser.should_callback():
                        try:
                            progress_callback(progress)
                        except Exception as e:
                            logger.warning(f"{log_prefix} Progress callback error: {e}")
                else:
                    # Log all other FFmpeg output at INFO so it appears in the TUI/logs
                    logger.info(f"{log_prefix} [FFmpeg] {line_str.strip()}")
        except Exception as e:
            logger.debug(f"{log_prefix} stderr reader error: {e}")
    
    def _monitor_stall_and_cancel(
        self,
        process: subprocess.Popen,
        state: Dict[str, Any],
        stall_timeout: float,
        grace_period: float,
        cancel_event: Optional[gevent.event.Event],
        job_dir: Optional[Path],
        log_prefix: str
    ) -> None:
        """Monitor for stalls and cancellation."""
        zombie_check_interval = 5
        iteration = 0
        
        while process.poll() is None:
            iteration += 1
            
            if iteration % zombie_check_interval == 0:
                try:
                    if sys.platform != "win32":
                        try:
                            os.kill(process.pid, 0)
                        except ProcessLookupError:
                            logger.warning(f"{log_prefix} Process {process.pid} no longer exists")
                            state["stalled"] = True
                            return
                        except PermissionError:
                            pass
                except Exception:
                    pass
            
            if cancel_event and cancel_event.is_set():
                state["cancelled"] = True
                logger.info(f"{log_prefix} Cancellation requested")
                self._graceful_terminate(process)
                return
            
            elapsed = time.time() - state["start_time"]
            time_since_progress = time.time() - state["last_progress_time"]
            
            if elapsed < grace_period:
                gevent.sleep(1.0)
                continue
            
            if time_since_progress > stall_timeout:
                if job_dir:
                    new_size, has_grown = self._check_file_growth(
                        job_dir, state["last_file_size"]
                    )
                    if has_grown:
                        state["last_progress_time"] = time.time()
                        state["last_file_size"] = new_size
                        logger.debug(f"{log_prefix} File growth detected, resetting stall timer")
                        gevent.sleep(1.0)
                        continue
                
                if state["stdout_bytes"] > 0:
                    state["last_progress_time"] = time.time()
                    state["stdout_bytes"] = 0
                    gevent.sleep(1.0)
                    continue
                
                state["stalled"] = True
                logger.error(f"{log_prefix} FFmpeg stalled for {stall_timeout:.0f}s, terminating")
                self._graceful_terminate(process)
                return
            
            gevent.sleep(1.0)
    
    def _check_file_growth(self, job_dir: Path, last_size: int) -> Tuple[int, bool]:
        """Check if output files are growing."""
        try:
            total_size = 0
            for f in job_dir.glob("**/*"):
                if f.is_file():
                    total_size += f.stat().st_size
            return total_size, total_size > last_size
        except Exception:
            return last_size, False
    
    def _graceful_terminate(self, process: subprocess.Popen) -> None:
        """Gracefully terminate FFmpeg with platform-specific signals."""
        if process.poll() is not None:
            return
        
        try:
            if sys.platform == "win32":
                try:
                    process.send_signal(signal.CTRL_BREAK_EVENT)
                except (ProcessLookupError, OSError):
                    pass
            else:
                try:
                    process.send_signal(signal.SIGINT)
                except (ProcessLookupError, OSError):
                    pass
            
            try:
                gevent.with_timeout(5.0, process.wait)
                logger.debug("[FFmpeg] Terminated gracefully")
                return
            except gevent.Timeout:
                pass
            
            try:
                process.terminate()
                gevent.with_timeout(3.0, process.wait)
                logger.debug("[FFmpeg] Terminated with SIGTERM")
                return
            except (gevent.Timeout, ProcessLookupError, OSError):
                pass
            
            try:
                process.kill()
                process.wait()
                logger.warning("[FFmpeg] Killed forcefully")
            except (ProcessLookupError, OSError):
                pass
                
        except Exception as e:
            logger.warning(f"[FFmpeg] Error during termination: {e}")

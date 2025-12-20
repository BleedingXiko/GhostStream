"""
Main transcoding engine that orchestrates all transcoding operations.
"""

import asyncio
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Callable, List, Tuple, Dict, Any, Set

from ..models import OutputConfig, OutputFormat, VideoCodec, HWAccel, TranscodeMode
from ..hardware import get_capabilities
from ..config import get_config
from .models import MediaInfo, TranscodeProgress, QualityPreset
from .constants import (
    MAX_RETRIES, 
    RETRY_DELAY, 
    MIN_STALL_TIMEOUT, 
    STALL_TIMEOUT_PER_SEGMENT,
    MAX_RETRY_DELAY,
    TRANSIENT_INFINITE_RETRY,
    STDERR_BUFFER_SIZE,
    STDERR_EARLY_BUFFER_SIZE,
)
from .filters import FilterBuilder
from .encoders import EncoderSelector
from .probe import MediaProbe
from .commands import CommandBuilder
from .adaptive import HardwareProfiler, AdaptiveQualitySelector, SystemProfile

# Import modular components
from .error_classifier import ErrorClassifier, get_error_classifier, FFmpegError, FFMPEG_ERROR_MAP
from .job_context import JobContext, JobRegistry, JobRegistryEntry
from .ffmpeg_runner import FFmpegRunner, ProgressParser, StallConfig
from .hls import HLSPlaylistGenerator, HLSConfig, StreamingRecommendations

# Thread pool for blocking I/O operations (cleanup, etc.)
# Scale workers based on CPU count
_io_workers = min(max(os.cpu_count() or 4, 2), 8)
_executor = ThreadPoolExecutor(max_workers=_io_workers, thread_name_prefix="ghoststream_io")

logger = logging.getLogger(__name__)

# Note: FFmpegError, FFMPEG_ERROR_MAP, JobContext, JobRegistry, JobRegistryEntry,
# ProgressParser are now imported from modular files for cleaner architecture


class TranscodeEngine:
    """
    FFmpeg-based transcoding engine with modular architecture.
    
    Features:
    - Netflix-level HLS streaming with proper CODECS and BANDWIDTH
    - Hardware-aware adaptive bitrate with NVENC session management
    - Quality-optimized encoding with artifact reduction
    - Modular error classification and retry logic
    """
    
    def __init__(self):
        self.config = get_config()
        self.capabilities = get_capabilities(
            self.config.transcoding.ffmpeg_path,
            self.config.transcoding.max_concurrent_jobs
        )
        self.ffmpeg_path = self._find_ffmpeg()
        self.temp_dir = Path(self.config.transcoding.temp_directory)
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize modular components
        self.probe = MediaProbe(self._find_ffprobe())
        self.filter_builder = FilterBuilder(self.ffmpeg_path)
        self.encoder_selector = EncoderSelector(
            self.capabilities,
            self.config.hardware
        )
        self.command_builder = CommandBuilder(
            self.ffmpeg_path,
            self.encoder_selector,
            self.filter_builder,
            self.config.transcoding,
            self.config.hardware
        )
        
        # Initialize adaptive hardware profiling
        self.hardware_profiler = HardwareProfiler(self.capabilities)
        self._hardware_profile: Optional[SystemProfile] = None
        
        # Concurrency control: semaphore to enforce max concurrent transcodes
        max_concurrent = self.config.transcoding.max_concurrent_jobs
        self._transcode_semaphore = asyncio.Semaphore(max_concurrent)
        
        # Optional job registry for tracking active/queued jobs
        self._job_registry = JobRegistry()
        
        # Verbose FFmpeg stdout forwarding for debugging
        self._verbose_ffmpeg = os.environ.get('GHOSTSTREAM_FFMPEG_VERBOSE', '').lower() in ('1', 'true', 'yes')
        
        # Modular components
        self._error_classifier = get_error_classifier()
        self._ffmpeg_runner = FFmpegRunner(
            stall_config=StallConfig(
                base_timeout=max(MIN_STALL_TIMEOUT, self.config.transcoding.stall_timeout),
                timeout_per_segment=STALL_TIMEOUT_PER_SEGMENT,
            ),
            verbose=self._verbose_ffmpeg
        )
        
        # HLS generator for Netflix-quality playlists
        self._hls_generator = HLSPlaylistGenerator(HLSConfig(
            segment_duration=self.config.transcoding.segment_duration
        ))
    
    @property
    def hardware_profile(self) -> SystemProfile:
        """Get hardware profile (lazily initialized)."""
        if self._hardware_profile is None:
            self._hardware_profile = self.hardware_profiler.get_profile()
        return self._hardware_profile
    
    def get_adaptive_quality_selector(self) -> AdaptiveQualitySelector:
        """Get adaptive quality selector for current hardware."""
        return AdaptiveQualitySelector(self.hardware_profile)
    
    def get_optimal_presets(self, media_info: MediaInfo) -> List[QualityPreset]:
        """Get optimal quality presets for the source media given hardware limits."""
        selector = self.get_adaptive_quality_selector()
        return selector.get_optimal_presets(media_info)
    
    def should_transcode(self, media_info: MediaInfo) -> Tuple[bool, str]:
        """Determine if transcoding is needed based on hardware and source."""
        selector = self.get_adaptive_quality_selector()
        return selector.should_transcode(media_info)
    
    def _find_ffmpeg(self) -> str:
        """Find ffmpeg executable."""
        if self.config.transcoding.ffmpeg_path != "auto":
            return self.config.transcoding.ffmpeg_path
        
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg:
            return ffmpeg
        
        raise RuntimeError("FFmpeg not found")
    
    def _find_ffprobe(self) -> str:
        """Find ffprobe executable."""
        ffprobe = shutil.which("ffprobe")
        if ffprobe:
            return ffprobe
        return "ffprobe"
    
    async def get_media_info(self, source: str, retry_count: int = 0) -> MediaInfo:
        """Get media information using ffprobe with retry logic."""
        return await self.probe.get_media_info(source, retry_count)
    
    def build_hls_command(
        self,
        source: str,
        output_dir: Path,
        output_config: OutputConfig,
        start_time: float = 0,
        media_info: Optional[MediaInfo] = None,
        subtitles: Optional[List] = None
    ) -> Tuple[List[str], str]:
        """Build FFmpeg command for HLS output."""
        return self.command_builder.build_hls_command(
            source, output_dir, output_config, start_time, media_info, subtitles
        )
    
    def build_batch_command(
        self,
        source: str,
        output_path: Path,
        output_config: OutputConfig,
        start_time: float = 0,
        media_info: Optional[MediaInfo] = None,
        two_pass: bool = False,
        pass_num: int = 1,
        passlog_prefix: Optional[str] = None
    ) -> Tuple[List[str], str]:
        """Build FFmpeg command for batch transcoding."""
        return self.command_builder.build_batch_command(
            source, output_path, output_config, start_time, media_info,
            two_pass, pass_num, passlog_prefix
        )
    
    def build_abr_command(
        self,
        source: str,
        output_dir: Path,
        output_config: OutputConfig,
        media_info: MediaInfo,
        start_time: float = 0,
        variants: Optional[List[QualityPreset]] = None,
        subtitles: Optional[List] = None
    ) -> Tuple[List[str], str, List[QualityPreset]]:
        """Build FFmpeg command for ABR HLS."""
        return self.command_builder.build_abr_command(
            source, output_dir, output_config, media_info, start_time, variants, subtitles
        )
    
    def get_abr_variants(self, media_info: MediaInfo) -> List[QualityPreset]:
        """Get appropriate ABR variants based on source resolution."""
        return self.command_builder.get_abr_variants(media_info)
    
    def generate_master_playlist(
        self,
        output_dir: Path,
        variants: List[QualityPreset]
    ) -> str:
        """Generate HLS master playlist for ABR variants."""
        return self.command_builder.generate_master_playlist(output_dir, variants)
    
    def _calculate_stall_timeout(self, media_info: MediaInfo) -> float:
        """
        Calculate dynamic stall timeout based on content.
        
        Longer content or higher resolution may need more time per segment.
        Minimum 120s, scales with segment duration and resolution.
        """
        base_timeout = max(
            MIN_STALL_TIMEOUT,
            self.config.transcoding.stall_timeout
        )
        
        # Scale with segment duration (default 4s segments)
        segment_duration = self.config.transcoding.segment_duration
        segment_factor = STALL_TIMEOUT_PER_SEGMENT * segment_duration
        
        # Scale with resolution (4K needs more time)
        resolution_factor = 1.0
        if media_info.width >= 3840:
            resolution_factor = 2.0
        elif media_info.width >= 1920:
            resolution_factor = 1.5
        
        timeout = base_timeout + (segment_factor * resolution_factor)
        
        logger.debug(f"[Transcode] Dynamic stall timeout: {timeout:.0f}s "
                    f"(base={base_timeout}, segment={segment_duration}s, res_factor={resolution_factor})")
        
        return timeout
    
    def _get_stall_grace_period(self, media_info: MediaInfo) -> float:
        """
        Get grace period before stall detection begins.
        
        First segments often take longer due to initialization,
        especially for large MKVs or slow network sources.
        """
        # Base grace period
        grace = 30.0
        
        # Add time for 4K content
        if media_info.width >= 3840:
            grace += 30.0
        elif media_info.width >= 1920:
            grace += 15.0
        
        # Add time for HDR content (more complex processing)
        if media_info.is_hdr:
            grace += 15.0
        
        return grace
    
    def _classify_error(self, error_msg: str) -> Tuple[Optional[FFmpegError], str]:
        """Classify FFmpeg error using the modular error classifier."""
        return self._error_classifier.classify(error_msg)
    
    def _is_hardware_error(self, error_msg: str) -> bool:
        """Check if error is hardware-related."""
        return self._error_classifier.is_hardware_error(error_msg)
    
    def _is_transient_error(self, error_msg: str) -> bool:
        """Check if error is transient (retryable)."""
        return self._error_classifier.is_transient_error(error_msg)
    
    def _resolve_output_extension(self, output_format: OutputFormat) -> str:
        """Resolve file extension for output format."""
        ext_map = {
            OutputFormat.MP4: ".mp4",
            OutputFormat.WEBM: ".webm",
            OutputFormat.MKV: ".mkv",
            OutputFormat.HLS: ".m3u8",
            OutputFormat.DASH: ".mpd",
        }
        return ext_map.get(output_format, ".mp4")
    
    def _check_file_growth(self, job_dir: Path, last_size: int) -> Tuple[int, bool]:
        """
        Check if output files are growing (indicates progress even without stderr).
        
        Returns:
            Tuple of (current_total_size, has_grown)
        """
        try:
            total_size = 0
            for f in job_dir.glob("**/*"):
                if f.is_file():
                    total_size += f.stat().st_size
            return total_size, total_size > last_size
        except Exception:
            return last_size, False
    
    async def _graceful_terminate(self, process: asyncio.subprocess.Process) -> None:
        """
        Gracefully terminate FFmpeg process with platform-specific signals.
        
        Uses SIGINT on Unix (allows FFmpeg to finalize) and CTRL_BREAK_EVENT on Windows.
        Falls back to SIGTERM/kill if graceful termination fails.
        """
        if process.returncode is not None:
            return  # Already terminated
        
        try:
            if sys.platform == "win32":
                # Windows: send CTRL_BREAK_EVENT for graceful shutdown
                try:
                    process.send_signal(signal.CTRL_BREAK_EVENT)
                except (ProcessLookupError, OSError):
                    pass
            else:
                # Unix: SIGINT allows FFmpeg to finalize current segment
                try:
                    process.send_signal(signal.SIGINT)
                except (ProcessLookupError, OSError):
                    pass
            
            # Wait for graceful shutdown
            try:
                await asyncio.wait_for(process.wait(), timeout=5.0)
                logger.debug("[Transcode] FFmpeg terminated gracefully")
                return
            except asyncio.TimeoutError:
                pass
            
            # Escalate to SIGTERM
            try:
                process.terminate()
                await asyncio.wait_for(process.wait(), timeout=3.0)
                logger.debug("[Transcode] FFmpeg terminated with SIGTERM")
                return
            except (asyncio.TimeoutError, ProcessLookupError, OSError):
                pass
            
            # Last resort: SIGKILL
            try:
                process.kill()
                await process.wait()
                logger.warning("[Transcode] FFmpeg killed forcefully")
            except (ProcessLookupError, OSError):
                pass
                
        except Exception as e:
            logger.warning(f"[Transcode] Error during process termination: {e}")
    
    async def _run_ffmpeg(
        self,
        cmd: List[str],
        media_info: MediaInfo,
        progress_callback: Optional[Callable[[TranscodeProgress], None]],
        cancel_event: Optional[asyncio.Event],
        stage: str = "transcoding",
        job_context: Optional[JobContext] = None
    ) -> Tuple[int, str]:
        """
        Run FFmpeg process with progress tracking.
        
        Uses separate async tasks for stdout and stderr to prevent deadlocks.
        Implements improved stall detection with grace period and file growth checks.
        
        Returns:
            Tuple of (return_code, error_output). Return code is -1 if process
            failed to start or was killed unexpectedly.
        """
        log_prefix = job_context.log_prefix if job_context else "[Transcode]"
        logger.info(f"{log_prefix} Running FFmpeg: {' '.join(cmd[:10])}...")
        
        # Calculate timeouts
        stall_timeout = self._calculate_stall_timeout(media_info)
        grace_period = self._get_stall_grace_period(media_info)
        
        # Spawn process
        process = await self._spawn_ffmpeg_process(cmd, log_prefix)
        if process is None:
            return -1, "Failed to start FFmpeg process"
        
        # Initialize state
        progress = TranscodeProgress(stage=stage)
        progress_parser = ProgressParser(throttle_interval=0.5)
        state = {
            "stderr_lines": [],
            "stderr_early": [],  # Preserve early errors separately
            "stdout_bytes": 0,
            "last_progress_time": time.time(),
            "last_file_size": 0,
            "stalled": False,
            "cancelled": False,
            "start_time": time.time(),
        }
        job_dir = job_context.job_dir if job_context else None
        
        # Create reader tasks
        stdout_task = asyncio.create_task(
            self._read_stdout(process, state, log_prefix)
        )
        stderr_task = asyncio.create_task(
            self._read_stderr(process, state, progress, progress_parser, 
                            media_info, progress_callback, log_prefix)
        )
        monitor_task = asyncio.create_task(
            self._monitor_stall_and_cancel(
                process, state, stall_timeout, grace_period, 
                cancel_event, job_dir, log_prefix
            )
        )
        
        try:
            await asyncio.gather(stdout_task, stderr_task, monitor_task, return_exceptions=True)
        except Exception as e:
            logger.warning(f"{log_prefix} Error during FFmpeg execution: {e}")
        
        # Ensure process has terminated
        try:
            await asyncio.wait_for(process.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            logger.error(f"{log_prefix} FFmpeg did not exit, force killing")
            await self._graceful_terminate(process)
        
        # Determine return code
        return_code = process.returncode if process.returncode is not None else -1
        
        # Build error output with context
        error_output = "".join(state["stderr_lines"])
        if state["stalled"]:
            error_output = f"[STALLED after {stall_timeout:.0f}s] " + error_output
        if state["cancelled"]:
            error_output = "[CANCELLED] " + error_output
        
        return return_code, error_output
    
    async def _spawn_ffmpeg_process(
        self, 
        cmd: List[str], 
        log_prefix: str
    ) -> Optional[asyncio.subprocess.Process]:
        """Spawn FFmpeg subprocess with platform-specific options."""
        try:
            kwargs: Dict[str, Any] = {
                "stdout": asyncio.subprocess.PIPE,
                "stderr": asyncio.subprocess.PIPE,
            }
            if sys.platform == "win32":
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            
            return await asyncio.create_subprocess_exec(*cmd, **kwargs)
        except Exception as e:
            logger.error(f"{log_prefix} Failed to start FFmpeg: {e}")
            return None
    
    async def _read_stdout(
        self,
        process: asyncio.subprocess.Process,
        state: Dict[str, Any],
        log_prefix: str
    ) -> None:
        """Read stdout in separate task to prevent pipe blocking."""
        try:
            while True:
                chunk = await process.stdout.read(4096)
                if not chunk:
                    break
                state["stdout_bytes"] += len(chunk)
                
                # Verbose forwarding if enabled
                if self._verbose_ffmpeg:
                    logger.debug(f"{log_prefix} stdout: {chunk.decode('utf-8', errors='ignore')[:200]}")
        except Exception as e:
            logger.debug(f"{log_prefix} stdout reader error: {e}")
    
    async def _read_stderr(
        self,
        process: asyncio.subprocess.Process,
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
                line = await process.stderr.readline()
                if not line:
                    break
                
                line_str = line.decode("utf-8", errors="ignore")
                
                # Preserve early errors in separate buffer (first N lines)
                if len(state["stderr_early"]) < STDERR_EARLY_BUFFER_SIZE:
                    state["stderr_early"].append(line_str)
                
                # Rolling buffer for recent lines
                state["stderr_lines"].append(line_str)
                
                # Keep last N lines to avoid memory growth while preserving more context
                if len(state["stderr_lines"]) > STDERR_BUFFER_SIZE:
                    state["stderr_lines"].pop(0)
                
                # Parse progress using centralized parser
                if parser.should_parse(line_str):
                    state["last_progress_time"] = time.time()
                    parser.parse(line_str, progress, media_info)
                    
                    # Throttled callback
                    if progress_callback and parser.should_callback():
                        try:
                            progress_callback(progress)
                        except Exception as e:
                            logger.warning(f"{log_prefix} Progress callback error: {e}")
        except Exception as e:
            logger.debug(f"{log_prefix} stderr reader error: {e}")
    
    async def _monitor_stall_and_cancel(
        self,
        process: asyncio.subprocess.Process,
        state: Dict[str, Any],
        stall_timeout: float,
        grace_period: float,
        cancel_event: Optional[asyncio.Event],
        job_dir: Optional[Path],
        log_prefix: str
    ) -> None:
        """
        Monitor for stalls and cancellation with improved detection.
        
        Uses grace period before stall detection begins and checks
        file growth as additional progress indicator. Also detects
        zombie processes that have exited but not been reaped.
        """
        zombie_check_interval = 5  # Check for zombie every 5 iterations
        iteration = 0
        
        while process.returncode is None:
            iteration += 1
            
            # Zombie process detection - check if process actually exited
            # but returncode hasn't been updated (zombie state)
            if iteration % zombie_check_interval == 0:
                try:
                    # Non-blocking check if process is still running
                    if sys.platform != "win32":
                        import os as os_module
                        try:
                            os_module.kill(process.pid, 0)  # Signal 0 = check existence
                        except ProcessLookupError:
                            # Process doesn't exist - zombie or exited
                            logger.warning(f"{log_prefix} Process {process.pid} no longer exists (zombie)")
                            state["stalled"] = True
                            return
                        except PermissionError:
                            pass  # Process exists but we can't signal it
                except Exception:
                    pass  # Ignore errors in zombie detection
            
            # Check cancellation - terminate immediately
            if cancel_event and cancel_event.is_set():
                state["cancelled"] = True
                logger.info(f"{log_prefix} Cancellation requested, terminating FFmpeg")
                await self._graceful_terminate(process)
                return
            
            elapsed = time.time() - state["start_time"]
            time_since_progress = time.time() - state["last_progress_time"]
            
            # Skip stall detection during grace period
            if elapsed < grace_period:
                await asyncio.sleep(1.0)
                continue
            
            # Check for stall
            if time_since_progress > stall_timeout:
                # Secondary check: file growth
                if job_dir:
                    new_size, has_grown = self._check_file_growth(
                        job_dir, state["last_file_size"]
                    )
                    if has_grown:
                        # Files are growing, update progress time
                        state["last_progress_time"] = time.time()
                        state["last_file_size"] = new_size
                        logger.debug(f"{log_prefix} File growth detected, resetting stall timer")
                        await asyncio.sleep(1.0)
                        continue
                
                # Also check stdout bytes as progress indicator
                if state["stdout_bytes"] > 0:
                    # Some progress via stdout
                    state["last_progress_time"] = time.time()
                    state["stdout_bytes"] = 0  # Reset for next check
                    await asyncio.sleep(1.0)
                    continue
                
                state["stalled"] = True
                logger.error(f"{log_prefix} FFmpeg stalled for {stall_timeout:.0f}s, terminating")
                await self._graceful_terminate(process)
                return
            
            await asyncio.sleep(1.0)
    
    def _parse_progress(
        self,
        line: str,
        progress: TranscodeProgress,
        media_info: MediaInfo
    ) -> None:
        """
        Parse FFmpeg progress output with hardened regex patterns.
        
        Handles various FFmpeg output formats and edge cases.
        """
        # Frame count - multiple possible formats
        match = re.search(r"frame=\s*(\d+)", line)
        if match:
            try:
                progress.frame = int(match.group(1))
            except (ValueError, TypeError):
                pass
        
        # FPS - may have decimals or be "N/A"
        match = re.search(r"fps=\s*([\d.]+|N/A)", line)
        if match and match.group(1) != "N/A":
            try:
                progress.fps = float(match.group(1))
            except (ValueError, TypeError):
                pass
        
        # Bitrate - various formats like "1234kbits/s", "1.2Mbits/s", "N/A"
        match = re.search(r"bitrate=\s*([\d.]+\s*[kMG]?bits/s|N/A)", line)
        if match and match.group(1) != "N/A":
            progress.bitrate = match.group(1).strip()
        
        # Size - may be in kB, MB, or bytes
        match = re.search(r"size=\s*(\d+)\s*(kB|MB|B)?", line)
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
            except (ValueError, TypeError):
                pass
        
        # Time - format HH:MM:SS.ms or MM:SS.ms
        match = re.search(r"time=\s*(\d+):(\d+):(\d+\.?\d*)", line)
        if match:
            try:
                h, m, s = match.groups()
                progress.time = int(h) * 3600 + int(m) * 60 + float(s)
            except (ValueError, TypeError):
                pass
        else:
            # Try MM:SS.ms format
            match = re.search(r"time=\s*(\d+):(\d+\.?\d*)", line)
            if match:
                try:
                    m, s = match.groups()
                    progress.time = int(m) * 60 + float(s)
                except (ValueError, TypeError):
                    pass
        
        # Speed - may be "N/A" or have various decimal formats
        match = re.search(r"speed=\s*([\d.]+)x", line)
        if match:
            try:
                progress.speed = float(match.group(1))
            except (ValueError, TypeError):
                pass
        
        # Calculate percentage
        if media_info.duration > 0 and progress.time > 0:
            progress.percent = min(99.9, (progress.time / media_info.duration) * 100)
    
    async def _prepare_job(self, job_id: str, source: str) -> Tuple[Optional[MediaInfo], Optional[Path], Optional[str]]:
        """
        Prepare job directory and get media info.
        
        Returns:
            Tuple of (media_info, job_dir, error_message)
        """
        media_info = await self.get_media_info(source)
        
        # Validate duration is reasonable
        if media_info.duration <= 0:
            return None, None, f"Failed to get media info from: {source}. Check URL accessibility."
        
        # Sanity check: reject unreasonably long durations (>48 hours = likely corrupt metadata)
        MAX_DURATION = 48 * 3600  # 48 hours in seconds
        if media_info.duration > MAX_DURATION:
            return None, None, f"Media duration too large ({media_info.duration}s). Possible corrupt metadata."
        
        job_dir = self.temp_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        
        return media_info, job_dir, None
    
    def _build_transcode_command(
        self,
        mode: TranscodeMode,
        source: str,
        job_dir: Path,
        output_config: OutputConfig,
        start_time: float,
        media_info: MediaInfo,
        subtitles: Optional[List] = None
    ) -> Tuple[List[str], str, str]:
        """
        Build the FFmpeg command for transcoding.
        
        Split into extension resolution + command building for clarity.
        Source is explicitly passed, NOT inferred from previous command.
        
        Returns:
            Tuple of (command, encoder_used, output_path)
        """
        if mode == TranscodeMode.STREAM:
            cmd, encoder_used = self.build_hls_command(
                source, job_dir, output_config, start_time, media_info, subtitles
            )
            output_path = str(job_dir / "master.m3u8")
        else:
            # Use dedicated extension resolver
            ext = self._resolve_output_extension(output_config.format)
            output_file = job_dir / f"output{ext}"
            
            cmd, encoder_used = self.build_batch_command(
                source, output_file, output_config, start_time, media_info
            )
            output_path = str(output_file)
        
        return cmd, encoder_used, output_path
    
    def _validate_hls_output(self, output_path: str, job_dir: Path) -> Tuple[bool, str]:
        """
        Validate HLS output: master playlist exists, has variants, segments exist.
        
        Includes proper segment sequence validation to detect missing segments.
        
        Returns:
            Tuple of (is_valid, error_message)
        """
        master_path = Path(output_path)
        
        # Check master playlist exists
        if not master_path.exists():
            return False, "Master playlist not found"
        
        # Check master playlist has content
        content = master_path.read_text()
        if not content.strip():
            return False, "Master playlist is empty"
        
        # Check for at least one variant/stream reference
        has_variant = False
        segment_patterns = []
        
        for line in content.split("\n"):
            line = line.strip()
            if line.endswith(".m3u8") or line.endswith(".ts"):
                has_variant = True
                segment_patterns.append(line)
        
        if not has_variant:
            # Check for direct segment references
            segment_files = list(job_dir.glob("*.ts"))
            if not segment_files:
                return False, "No variant playlists or segments found"
        
        # Verify at least one segment exists
        segment_files = list(job_dir.glob("*.ts")) + list(job_dir.glob("*/*.ts"))
        if not segment_files:
            return False, "No segment files generated"
        
        # Check first segment has content
        first_segment = segment_files[0]
        if first_segment.stat().st_size == 0:
            return False, f"Segment {first_segment.name} is empty"
        
        # Validate segment sequence (check for missing segments)
        seq_valid, seq_error = self._validate_segment_sequence(segment_files)
        if not seq_valid:
            return False, seq_error
        
        # Perform segment integrity check if enabled
        if self.config.transcoding.validate_segments:
            integrity_ok, integrity_msg = self._check_segment_integrity(segment_files)
            if not integrity_ok:
                return False, integrity_msg
        
        logger.debug(f"[Validate] HLS output valid: {len(segment_files)} segments")
        return True, ""
    
    def _validate_segment_sequence(self, segment_files: List[Path]) -> Tuple[bool, str]:
        """
        Validate HLS segment sequence for missing segments.
        
        Properly handles segment numbering to detect gaps.
        
        Returns:
            Tuple of (is_valid, error_message)
        """
        if len(segment_files) < 2:
            return True, ""  # Single segment, nothing to validate
        
        # Extract segment numbers safely
        segment_numbers = []
        for f in segment_files:
            matches = re.findall(r"(\d+)", f.name)
            if matches:
                try:
                    # Take the last number in filename (usually the sequence number)
                    segment_numbers.append(int(matches[-1]))
                except (ValueError, IndexError):
                    continue
        
        if len(segment_numbers) < 2:
            return True, ""  # Can't validate sequence
        
        # Sort and check for gaps
        segment_numbers.sort()
        expected_sequence = list(range(segment_numbers[0], segment_numbers[-1] + 1))
        
        if segment_numbers != expected_sequence:
            missing = set(expected_sequence) - set(segment_numbers)
            if missing:
                return False, f"Missing HLS segments: {sorted(missing)[:5]}{'...' if len(missing) > 5 else ''}"
        
        return True, ""
    
    def _validate_hls_bitrate_spacing(
        self, 
        variants: List[QualityPreset],
        min_ratio: float = 1.5
    ) -> Tuple[bool, List[str]]:
        """
        Sanity check for HLS bitrate spacing.
        
        Variants too close in bitrate can confuse adaptive players.
        
        Args:
            variants: List of quality presets
            min_ratio: Minimum ratio between adjacent bitrate levels
            
        Returns:
            Tuple of (is_valid, list_of_warnings)
        """
        if len(variants) < 2:
            return True, []
        
        warnings = []
        
        # Parse bitrates and sort
        def parse_bitrate(br: str) -> float:
            br = br.strip().upper()
            if br.endswith('M'):
                return float(br[:-1]) * 1000
            elif br.endswith('K'):
                return float(br[:-1])
            return float(br)
        
        bitrates = sorted(
            [(v.name, parse_bitrate(v.video_bitrate)) for v in variants],
            key=lambda x: x[1]
        )
        
        for i in range(1, len(bitrates)):
            lower_name, lower_br = bitrates[i-1]
            upper_name, upper_br = bitrates[i]
            
            if lower_br > 0:
                ratio = upper_br / lower_br
                if ratio < min_ratio:
                    warnings.append(
                        f"Variants '{lower_name}' ({lower_br:.0f}k) and '{upper_name}' "
                        f"({upper_br:.0f}k) are too close (ratio {ratio:.2f} < {min_ratio})"
                    )
        
        return len(warnings) == 0, warnings
    
    def _check_segment_integrity(self, segment_files: List[Path]) -> Tuple[bool, str]:
        """
        Check integrity of segment files before reporting success.
        
        Validates:
        - Minimum segment size
        - MPEG-TS sync byte presence
        - No truncated segments (reasonable size distribution)
        
        Returns:
            Tuple of (is_valid, error_message)
        """
        if not segment_files:
            return False, "No segments to check"
        
        min_segment_size = 1024  # 1KB minimum
        ts_sync_byte = b'\x47'  # MPEG-TS sync byte
        
        sizes = []
        for segment in segment_files[:10]:  # Check first 10 segments
            try:
                size = segment.stat().st_size
                sizes.append(size)
                
                # Check minimum size
                if size < min_segment_size:
                    return False, f"Segment {segment.name} too small: {size} bytes"
                
                # Check MPEG-TS sync byte
                with open(segment, 'rb') as f:
                    header = f.read(4)
                    if not header or header[0:1] != ts_sync_byte:
                        return False, f"Segment {segment.name} missing MPEG-TS sync byte"
                        
            except Exception as e:
                return False, f"Error checking segment {segment.name}: {e}"
        
        # Check for suspiciously small segments (possible truncation)
        if len(sizes) >= 3:
            avg_size = sum(sizes) / len(sizes)
            # Last segment can be smaller, but not by more than 95%
            for i, size in enumerate(sizes[:-1]):  # Exclude last segment
                if size < avg_size * 0.05:
                    return False, f"Segment {segment_files[i].name} suspiciously small ({size} vs avg {avg_size:.0f})"
        
        return True, ""
    
    def _validate_batch_output(self, output_path: str) -> Tuple[bool, str]:
        """
        Validate batch output file exists and has content.
        
        Returns:
            Tuple of (is_valid, error_message)
        """
        path = Path(output_path)
        
        if not path.exists():
            return False, "Output file not found"
        
        size = path.stat().st_size
        if size == 0:
            return False, "Output file is empty"
        
        # Basic sanity check - file should be at least 1KB
        if size < 1024:
            return False, f"Output file suspiciously small: {size} bytes"
        
        return True, ""
    
    def _validate_output(
        self,
        mode: TranscodeMode,
        output_path: str,
        job_dir: Path
    ) -> Tuple[bool, str]:
        """
        Validate transcoding output based on mode.
        
        Returns:
            Tuple of (is_valid, error_message)
        """
        if mode == TranscodeMode.STREAM:
            return self._validate_hls_output(output_path, job_dir)
        else:
            return self._validate_batch_output(output_path)
    
    async def _execute_with_retry(
        self,
        cmd: List[str],
        encoder_used: str,
        output_path: str,
        mode: TranscodeMode,
        job_context: JobContext,
        media_info: MediaInfo,
        current_config: OutputConfig,
        progress_callback: Optional[Callable[[TranscodeProgress], None]],
        cancel_event: Optional[asyncio.Event]
    ) -> Tuple[bool, str, Optional[str]]:
        """
        Execute FFmpeg with retry logic and per-job hardware fallback.
        
        Hardware fallback state is tracked per-job via JobContext,
        NOT globally. Uses proper FFmpeg error map for classification.
        
        Returns:
            Tuple of (success, output_path_or_error, hw_accel_used)
        """
        log_prefix = job_context.log_prefix
        retry_count = self.config.transcoding.retry_count
        source = job_context.source  # Explicit source, not extracted from cmd
        job_dir = job_context.job_dir
        
        for attempt in range(retry_count + 1):
            if cancel_event and cancel_event.is_set():
                return False, "Cancelled", None
            
            logger.info(f"{log_prefix} Attempt {attempt + 1}/{retry_count + 1} with encoder: {encoder_used}")
            
            return_code, error_output = await self._run_ffmpeg(
                cmd, media_info, progress_callback, cancel_event,
                job_context=job_context
            )
            
            if cancel_event and cancel_event.is_set():
                return False, "Cancelled", encoder_used
            
            if return_code == 0:
                # Validate output
                is_valid, validation_error = self._validate_output(mode, output_path, job_dir)
                
                if is_valid:
                    hw_accel_used = self.encoder_selector.detect_hw_accel_used(encoder_used)
                    logger.info(f"{log_prefix} Complete. HW accel: {hw_accel_used}")
                    
                    # Set progress to 100% on successful completion
                    if progress_callback:
                        final_progress = TranscodeProgress(
                            stage="complete",
                            percent=100.0,
                            time=media_info.duration
                        )
                        try:
                            progress_callback(final_progress)
                        except Exception:
                            pass
                    
                    return True, output_path, hw_accel_used
                else:
                    logger.warning(f"{log_prefix} FFmpeg returned success but validation failed: {validation_error}")
                    error_output = f"Validation failed: {validation_error}. " + error_output
            
            error_msg = error_output[-1000:] if error_output else "Unknown error"
            logger.warning(f"{log_prefix} FFmpeg failed (code {return_code}): {error_msg[:200]}")
            
            # Classify error using proper FFmpeg error map
            error_info, error_category = self._classify_error(error_msg)
            
            # Hardware fallback (per-job, not global)
            if not job_context.hw_fallback_attempted and error_category == "hardware":
                logger.info(f"{log_prefix} Hardware error detected, falling back to software")
                current_config.hw_accel = HWAccel.SOFTWARE
                job_context.hw_fallback_attempted = True
                
                # Mark hardware as problematic in encoder selector
                self.encoder_selector.mark_hw_failed(encoder_used)
                
                # Clean partial output
                await self._async_cleanup_dir(job_dir)
                
                # Rebuild command with software encoder using explicit source
                cmd, encoder_used, output_path = self._build_transcode_command(
                    mode, source, job_dir, current_config, 0, media_info
                )
                continue
            
            # Transient error retry using proper error classification with exponential backoff
            if self._is_transient_error(error_msg):
                # Calculate delay with exponential backoff and jitter
                import random
                base_delay = min(RETRY_DELAY * (2 ** attempt), MAX_RETRY_DELAY)
                jitter = random.uniform(0, base_delay * 0.1)  # 10% jitter
                delay = base_delay + jitter
                desc = error_info.description if error_info else "Transient error"
                
                # For transient errors, keep retrying indefinitely if configured
                if TRANSIENT_INFINITE_RETRY or attempt < retry_count:
                    logger.info(f"{log_prefix} {desc}, retrying in {delay:.1f}s (attempt {attempt + 1})...")
                    await asyncio.sleep(delay)
                    # Don't increment attempt counter for infinite retry mode - reset loop
                    if TRANSIENT_INFINITE_RETRY:
                        # Clean partial output before retry
                        await self._async_cleanup_dir(job_dir)
                        continue
                    continue
            
            # Non-transient error or retry limit reached
            return False, f"FFmpeg error: {error_msg}", encoder_used
        
        return False, "Max retries exceeded", None
    
    async def _async_cleanup_dir(self, dir_path: Path) -> None:
        """Asynchronously clean directory contents using thread executor."""
        loop = asyncio.get_event_loop()
        
        def cleanup():
            for f in dir_path.glob("*"):
                try:
                    if f.is_file():
                        f.unlink()
                    elif f.is_dir():
                        shutil.rmtree(f, ignore_errors=True)
                except Exception as e:
                    logger.debug(f"Failed to clean {f}: {e}")
        
        await loop.run_in_executor(_executor, cleanup)
    
    async def transcode(
        self,
        job_id: str,
        source: str,
        mode: TranscodeMode,
        output_config: OutputConfig,
        start_time: float = 0,
        progress_callback: Optional[Callable[[TranscodeProgress], None]] = None,
        cancel_event: Optional[asyncio.Event] = None,
        subtitles: Optional[List] = None
    ) -> Tuple[bool, str, Optional[str]]:
        """
        Execute transcoding with retry logic and hardware fallback.
        
        Uses semaphore to enforce max concurrent transcodes.
        Tracks job in registry and ensures cleanup on all exception paths.
        
        Returns:
            Tuple of (success, output_path_or_error, hw_accel_used)
        """
        log_prefix = f"[Job:{job_id[:8]}]"
        job_dir: Optional[Path] = None
        
        # Register job
        await self._job_registry.register(job_id, source)
        
        # Acquire semaphore to enforce max concurrent transcodes
        async with self._transcode_semaphore:
            await self._job_registry.update_status(job_id, "running")
            
            try:
                # Prepare job
                media_info, job_dir, error = await self._prepare_job(job_id, source)
                if error:
                    await self._job_registry.update_status(job_id, "failed")
                    return False, error, None
                
                # Create job context for per-job state tracking
                job_context = JobContext(
                    job_id=job_id,
                    source=source,
                    job_dir=job_dir
                )
                
                current_config = OutputConfig(**output_config.model_dump())
                
                # Build command
                cmd, encoder_used, output_path = self._build_transcode_command(
                    mode, source, job_dir, current_config, start_time, media_info, subtitles
                )
                
                await self._job_registry.update_status(job_id, "running", encoder=encoder_used)
                
                # Execute with retry
                success, result, hw_accel = await self._execute_with_retry(
                    cmd, encoder_used, output_path, mode, job_context, media_info,
                    current_config, progress_callback, cancel_event
                )
                
                # Update registry with final status
                final_status = "completed" if success else "failed"
                await self._job_registry.update_status(job_id, final_status, progress=100.0 if success else 0.0)
                
                return success, result, hw_accel
                
            except asyncio.CancelledError:
                await self._job_registry.update_status(job_id, "cancelled")
                # Cleanup on cancellation
                if job_dir and job_dir.exists():
                    await self._async_cleanup_dir(job_dir)
                return False, "Cancelled", None
                
            except Exception as e:
                logger.exception(f"{log_prefix} Unexpected error: {e}")
                await self._job_registry.update_status(job_id, "failed")
                # Cleanup on exception (including spawn errors)
                if job_dir and job_dir.exists():
                    await self._async_cleanup_dir(job_dir)
                return False, str(e), None
            
            finally:
                # Always remove from registry after completion
                await self._job_registry.remove(job_id)
    
    async def transcode_abr(
        self,
        job_id: str,
        source: str,
        output_config: OutputConfig,
        start_time: float = 0,
        progress_callback: Optional[Callable[[TranscodeProgress], None]] = None,
        cancel_event: Optional[asyncio.Event] = None,
        subtitles: Optional[List] = None
    ) -> Tuple[bool, str, Optional[str]]:
        """
        Execute ABR transcoding with multiple quality variants.
        
        Uses semaphore to enforce max concurrent transcodes.
        Includes bitrate spacing validation and cleanup on all exception paths.
        
        Returns:
            Tuple of (success, master_playlist_path_or_error, hw_accel_used)
        """
        log_prefix = f"[Job:{job_id[:8]}]"
        job_dir: Optional[Path] = None
        
        # Register job
        await self._job_registry.register(job_id, source)
        
        # Acquire semaphore to enforce max concurrent transcodes
        async with self._transcode_semaphore:
            await self._job_registry.update_status(job_id, "running")
            
            try:
                media_info = await self.get_media_info(source)
                if media_info.duration == 0:
                    await self._job_registry.update_status(job_id, "failed")
                    return False, f"Failed to get media info from: {source}", None
                
                job_dir = self.temp_dir / job_id
                job_dir.mkdir(parents=True, exist_ok=True)
                
                # Create job context
                job_context = JobContext(
                    job_id=job_id,
                    source=source,
                    job_dir=job_dir
                )
                
                current_config = OutputConfig(**output_config.model_dump())
                
                # Get hardware-optimized variants
                variants = self.get_optimal_presets(media_info)
                
                cmd, encoder_used, variants = self.build_abr_command(
                    source, job_dir, current_config, media_info, start_time, variants, subtitles
                )
                
                # Validate bitrate spacing
                spacing_ok, spacing_warnings = self._validate_hls_bitrate_spacing(variants)
                if not spacing_ok:
                    for warning in spacing_warnings:
                        logger.warning(f"{log_prefix} {warning}")
                
                await self._job_registry.update_status(job_id, "running", encoder=encoder_used)
                logger.info(f"{log_prefix} Starting ABR transcode with {len(variants)} variants")
                
                return_code, error_output = await self._run_ffmpeg(
                    cmd, media_info, progress_callback, cancel_event,
                    job_context=job_context
                )
                
                if cancel_event and cancel_event.is_set():
                    await self._job_registry.update_status(job_id, "cancelled")
                    return False, "Cancelled", encoder_used
                
                if return_code == 0:
                    master_path = job_dir / "master.m3u8"
                    if master_path.exists():
                        hw_accel = self.encoder_selector.detect_hw_accel_used(encoder_used)
                        logger.info(f"{log_prefix} ABR complete with {len(variants)} variants")
                        await self._job_registry.update_status(job_id, "completed", progress=100.0)
                        
                        # Set progress to 100% on completion
                        if progress_callback:
                            final_progress = TranscodeProgress(
                                stage="complete",
                                percent=100.0,
                                time=media_info.duration
                            )
                            try:
                                progress_callback(final_progress)
                            except Exception:
                                pass
                        
                        return True, str(master_path), hw_accel
                
                # Log the specific error before falling back
                error_msg = error_output[-1000:] if error_output else "Unknown error"
                logger.warning(f"{log_prefix} ABR failed (code {return_code}): {error_msg[:200]}...")
                
                logger.warning(f"{log_prefix} ABR failed, falling back to single quality")
                # Remove from registry before recursive call (it will re-register)
                await self._job_registry.remove(job_id)
                
                return await self.transcode(
                    job_id, source, TranscodeMode.STREAM, output_config,
                    start_time, progress_callback, cancel_event
                )
                
            except asyncio.CancelledError:
                await self._job_registry.update_status(job_id, "cancelled")
                if job_dir and job_dir.exists():
                    await self._async_cleanup_dir(job_dir)
                return False, "Cancelled", None
                
            except Exception as e:
                logger.exception(f"{log_prefix} ABR error: {e}")
                await self._job_registry.update_status(job_id, "failed")
                # Cleanup on exception
                if job_dir and job_dir.exists():
                    await self._async_cleanup_dir(job_dir)
                return False, str(e), None
            
            finally:
                # Always remove from registry after completion
                await self._job_registry.remove(job_id)
    
    def cleanup_job(self, job_id: str) -> None:
        """Clean up job files (sync version for compatibility)."""
        job_dir = self.temp_dir / job_id
        if job_dir.exists():
            try:
                shutil.rmtree(job_dir, ignore_errors=True)
                logger.info(f"Cleaned up job directory: {job_dir}")
            except Exception as e:
                logger.warning(f"Failed to cleanup job {job_id}: {e}")
    
    async def cleanup_job_async(self, job_id: str) -> None:
        """
        Asynchronously clean up job files using thread executor.
        
        Prevents blocking the event loop during large directory deletions.
        Also removes job from registry if present.
        """
        # Remove from registry
        await self._job_registry.remove(job_id)
        
        job_dir = self.temp_dir / job_id
        if not job_dir.exists():
            return
        
        loop = asyncio.get_event_loop()
        
        def do_cleanup():
            try:
                shutil.rmtree(job_dir, ignore_errors=True)
                logger.info(f"Cleaned up job directory: {job_dir}")
            except Exception as e:
                logger.warning(f"Failed to cleanup job {job_id}: {e}")
        
        await loop.run_in_executor(_executor, do_cleanup)
    
    async def get_active_jobs(self) -> List[JobRegistryEntry]:
        """Get list of active (queued/running) jobs from the registry."""
        return await self._job_registry.get_active_jobs()
    
    async def get_job_status(self, job_id: str) -> Optional[JobRegistryEntry]:
        """Get status of a specific job from the registry."""
        return await self._job_registry.get_job(job_id)
    
    def get_active_job_count(self) -> int:
        """Get count of currently running jobs (non-async for quick checks)."""
        return self._job_registry.get_active_count()
    
    @property
    def max_concurrent_jobs(self) -> int:
        """Get the maximum number of concurrent transcodes allowed."""
        return self.config.transcoding.max_concurrent_jobs

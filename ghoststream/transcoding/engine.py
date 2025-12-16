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
from pathlib import Path
from typing import Optional, Callable, List, Tuple, Dict, Any

from ..models import OutputConfig, OutputFormat, VideoCodec, HWAccel, TranscodeMode
from ..hardware import get_capabilities
from ..config import get_config
from .models import MediaInfo, TranscodeProgress, QualityPreset
from .constants import MAX_RETRIES, RETRY_DELAY, MIN_STALL_TIMEOUT, STALL_TIMEOUT_PER_SEGMENT
from .filters import FilterBuilder
from .encoders import EncoderSelector
from .probe import MediaProbe
from .commands import CommandBuilder
from .adaptive import HardwareProfiler, AdaptiveQualitySelector, SystemProfile

# Thread pool for blocking I/O operations (cleanup, etc.)
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="ghoststream_io")

logger = logging.getLogger(__name__)


class TranscodeEngine:
    """FFmpeg-based transcoding engine with modular architecture."""
    
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
        
        # Track hardware fallback state
        self._hw_fallback_active = False
    
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
        media_info: Optional[MediaInfo] = None
    ) -> Tuple[List[str], str]:
        """Build FFmpeg command for HLS output."""
        return self.command_builder.build_hls_command(
            source, output_dir, output_config, start_time, media_info
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
        start_time: float = 0
    ) -> Tuple[List[str], str, List[QualityPreset]]:
        """Build FFmpeg command for ABR HLS."""
        return self.command_builder.build_abr_command(
            source, output_dir, output_config, media_info, start_time
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
        stage: str = "transcoding"
    ) -> Tuple[int, str]:
        """
        Run FFmpeg process with progress tracking.
        
        Uses separate async tasks for stdout and stderr to prevent deadlocks.
        Implements dynamic stall detection and graceful cancellation.
        
        Returns:
            Tuple of (return_code, error_output). Return code is -1 if process
            failed to start or was killed unexpectedly.
        """
        logger.info(f"[Transcode] Running FFmpeg: {' '.join(cmd[:10])}...")
        
        # Calculate dynamic stall timeout
        stall_timeout = self._calculate_stall_timeout(media_info)
        
        # Create process with separate pipes
        try:
            # On Windows, use CREATE_NEW_PROCESS_GROUP for proper signal handling
            kwargs: Dict[str, Any] = {
                "stdout": asyncio.subprocess.PIPE,
                "stderr": asyncio.subprocess.PIPE,
            }
            if sys.platform == "win32":
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            
            process = await asyncio.create_subprocess_exec(*cmd, **kwargs)
        except Exception as e:
            logger.error(f"[Transcode] Failed to start FFmpeg: {e}")
            return -1, str(e)
        
        progress = TranscodeProgress(stage=stage)
        stderr_lines: List[str] = []
        stdout_data: List[bytes] = []
        last_progress_time = time.time()
        stalled = False
        cancelled = False
        
        async def read_stdout():
            """Read stdout in separate task to prevent pipe blocking."""
            nonlocal stdout_data
            try:
                while True:
                    chunk = await process.stdout.read(4096)
                    if not chunk:
                        break
                    stdout_data.append(chunk)
            except Exception as e:
                logger.debug(f"[Transcode] stdout reader error: {e}")
        
        async def read_stderr():
            """Read stderr and parse progress in separate task."""
            nonlocal last_progress_time, stalled
            try:
                while True:
                    line = await process.stderr.readline()
                    if not line:
                        break
                    
                    line_str = line.decode("utf-8", errors="ignore")
                    stderr_lines.append(line_str)
                    
                    # Keep only last 100 lines to avoid memory growth
                    if len(stderr_lines) > 100:
                        stderr_lines.pop(0)
                    
                    # Parse progress from FFmpeg output
                    if "frame=" in line_str or "size=" in line_str:
                        last_progress_time = time.time()
                        self._parse_progress(line_str, progress, media_info)
                        
                        if progress_callback:
                            try:
                                progress_callback(progress)
                            except Exception as e:
                                logger.warning(f"Progress callback error: {e}")
            except Exception as e:
                logger.debug(f"[Transcode] stderr reader error: {e}")
        
        async def monitor_stall_and_cancel():
            """Monitor for stalls and cancellation requests."""
            nonlocal stalled, cancelled
            while process.returncode is None:
                # Check cancellation
                if cancel_event and cancel_event.is_set():
                    cancelled = True
                    logger.info("[Transcode] Cancellation requested, terminating FFmpeg")
                    await self._graceful_terminate(process)
                    return
                
                # Check stall
                if time.time() - last_progress_time > stall_timeout:
                    stalled = True
                    logger.error(f"[Transcode] FFmpeg stalled for {stall_timeout:.0f}s, terminating")
                    await self._graceful_terminate(process)
                    return
                
                await asyncio.sleep(1.0)
        
        # Run all tasks concurrently
        stdout_task = asyncio.create_task(read_stdout())
        stderr_task = asyncio.create_task(read_stderr())
        monitor_task = asyncio.create_task(monitor_stall_and_cancel())
        
        try:
            # Wait for process to complete or be terminated
            await asyncio.gather(stdout_task, stderr_task, monitor_task, return_exceptions=True)
        except Exception as e:
            logger.warning(f"[Transcode] Error during FFmpeg execution: {e}")
        
        # Ensure process has terminated
        try:
            await asyncio.wait_for(process.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            logger.error("[Transcode] FFmpeg did not exit, force killing")
            await self._graceful_terminate(process)
        
        # Determine return code with safe fallback
        return_code = process.returncode
        if return_code is None:
            return_code = -1
        
        # Annotate error output with context
        error_output = "".join(stderr_lines)
        if stalled:
            error_output = f"[STALLED after {stall_timeout:.0f}s] " + error_output
        if cancelled:
            error_output = "[CANCELLED] " + error_output
        
        return return_code, error_output
    
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
        if media_info.duration == 0:
            return None, None, f"Failed to get media info from: {source}. Check URL accessibility."
        
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
        media_info: MediaInfo
    ) -> Tuple[List[str], str, str]:
        """
        Build the FFmpeg command for transcoding.
        
        Returns:
            Tuple of (command, encoder_used, output_path)
        """
        if mode == TranscodeMode.STREAM:
            cmd, encoder_used = self.build_hls_command(
                source, job_dir, output_config, start_time, media_info
            )
            output_path = str(job_dir / "master.m3u8")
        else:
            ext_map = {
                OutputFormat.MP4: ".mp4",
                OutputFormat.WEBM: ".webm",
                OutputFormat.MKV: ".mkv",
                OutputFormat.HLS: ".m3u8",
                OutputFormat.DASH: ".mpd",
            }
            ext = ext_map.get(output_config.format, ".mp4")
            output_file = job_dir / f"output{ext}"
            
            cmd, encoder_used = self.build_batch_command(
                source, output_file, output_config, start_time, media_info
            )
            output_path = str(output_file)
        
        return cmd, encoder_used, output_path
    
    def _validate_hls_output(self, output_path: str, job_dir: Path) -> Tuple[bool, str]:
        """
        Validate HLS output: master playlist exists, has variants, segments exist.
        
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
        
        # Perform segment integrity check if enabled
        if self.config.transcoding.validate_segments:
            integrity_ok, integrity_msg = self._check_segment_integrity(segment_files)
            if not integrity_ok:
                return False, integrity_msg
        
        logger.debug(f"[Validate] HLS output valid: {len(segment_files)} segments")
        return True, ""
    
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
        job_dir: Path,
        media_info: MediaInfo,
        current_config: OutputConfig,
        progress_callback: Optional[Callable[[TranscodeProgress], None]],
        cancel_event: Optional[asyncio.Event]
    ) -> Tuple[bool, str, Optional[str]]:
        """
        Execute FFmpeg with retry logic and hardware fallback.
        
        Returns:
            Tuple of (success, output_path_or_error, hw_accel_used)
        """
        fallback_attempted = self._hw_fallback_active
        retry_count = self.config.transcoding.retry_count
        
        for attempt in range(retry_count + 1):
            if cancel_event and cancel_event.is_set():
                return False, "Cancelled", None
            
            logger.info(f"[Transcode] Attempt {attempt + 1}/{retry_count + 1} with encoder: {encoder_used}")
            
            return_code, error_output = await self._run_ffmpeg(
                cmd, media_info, progress_callback, cancel_event
            )
            
            if cancel_event and cancel_event.is_set():
                return False, "Cancelled", encoder_used
            
            if return_code == 0:
                # Validate output
                is_valid, validation_error = self._validate_output(mode, output_path, job_dir)
                
                if is_valid:
                    hw_accel_used = self.encoder_selector.detect_hw_accel_used(encoder_used)
                    logger.info(f"[Transcode] Complete. HW accel: {hw_accel_used}")
                    return True, output_path, hw_accel_used
                else:
                    logger.warning(f"[Transcode] FFmpeg returned success but validation failed: {validation_error}")
                    error_output = f"Validation failed: {validation_error}. " + error_output
            
            error_msg = error_output[-1000:] if error_output else "Unknown error"
            logger.warning(f"[Transcode] FFmpeg failed (code {return_code}): {error_msg[:200]}")
            
            # Hardware fallback
            if not fallback_attempted and self.encoder_selector.is_hw_error(error_msg):
                logger.info("[Transcode] Hardware error detected, falling back to software")
                current_config.hw_accel = HWAccel.SOFTWARE
                fallback_attempted = True
                self._hw_fallback_active = True  # Update global state
                
                # Mark hardware as problematic in encoder selector
                self.encoder_selector.mark_hw_failed(encoder_used)
                
                # Clean partial output
                await self._async_cleanup_dir(job_dir)
                
                # Rebuild command with software encoder
                cmd, encoder_used, output_path = self._build_transcode_command(
                    mode, cmd[cmd.index("-i") + 1], job_dir, current_config, 0, media_info
                )
                continue
            
            # Transient error retry
            transient_errors = ["connection", "timeout", "refused", "temporary", "resource", "network"]
            if attempt < retry_count and any(err in error_msg.lower() for err in transient_errors):
                delay = RETRY_DELAY * (attempt + 1)
                logger.info(f"[Transcode] Transient error, retrying in {delay}s...")
                await asyncio.sleep(delay)
                continue
            
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
        cancel_event: Optional[asyncio.Event] = None
    ) -> Tuple[bool, str, Optional[str]]:
        """
        Execute transcoding with retry logic and hardware fallback.
        
        Returns:
            Tuple of (success, output_path_or_error, hw_accel_used)
        """
        # Prepare job
        media_info, job_dir, error = await self._prepare_job(job_id, source)
        if error:
            return False, error, None
        
        current_config = OutputConfig(**output_config.model_dump())
        
        try:
            # Build command
            cmd, encoder_used, output_path = self._build_transcode_command(
                mode, source, job_dir, current_config, start_time, media_info
            )
            
            # Execute with retry
            return await self._execute_with_retry(
                cmd, encoder_used, output_path, mode, job_dir, media_info,
                current_config, progress_callback, cancel_event
            )
            
        except asyncio.CancelledError:
            return False, "Cancelled", None
        except Exception as e:
            logger.exception(f"[Transcode] Unexpected error: {e}")
            return False, str(e), None
    
    async def transcode_abr(
        self,
        job_id: str,
        source: str,
        output_config: OutputConfig,
        start_time: float = 0,
        progress_callback: Optional[Callable[[TranscodeProgress], None]] = None,
        cancel_event: Optional[asyncio.Event] = None
    ) -> Tuple[bool, str, Optional[str]]:
        """
        Execute ABR transcoding with multiple quality variants.
        
        Returns:
            Tuple of (success, master_playlist_path_or_error, hw_accel_used)
        """
        media_info = await self.get_media_info(source)
        if media_info.duration == 0:
            return False, f"Failed to get media info from: {source}", None
        
        job_dir = self.temp_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        
        current_config = OutputConfig(**output_config.model_dump())
        
        try:
            cmd, encoder_used, variants = self.build_abr_command(
                source, job_dir, current_config, media_info, start_time
            )
            
            logger.info(f"[Transcode] Starting ABR transcode with {len(variants)} variants")
            
            return_code, error_output = await self._run_ffmpeg(
                cmd, media_info, progress_callback, cancel_event
            )
            
            if cancel_event and cancel_event.is_set():
                return False, "Cancelled", encoder_used
            
            if return_code == 0:
                master_path = job_dir / "master.m3u8"
                if master_path.exists():
                    hw_accel = self.encoder_selector.detect_hw_accel_used(encoder_used)
                    logger.info(f"[Transcode] ABR complete with {len(variants)} variants")
                    return True, str(master_path), hw_accel
            
            logger.warning("[Transcode] ABR failed, falling back to single quality")
            return await self.transcode(
                job_id, source, TranscodeMode.STREAM, output_config,
                start_time, progress_callback, cancel_event
            )
            
        except Exception as e:
            logger.exception(f"[Transcode] ABR error: {e}")
            return False, str(e), None
    
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
        """
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

"""
Main transcoding engine that orchestrates all transcoding operations — Specter-native (gevent).
"""

import logging
import os
import gevent
import gevent.event
import gevent.lock
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from ....contracts.api import HWAccel, OutputConfig, OutputFormat, TranscodeMode
from ...domain.streaming.hls import HLSPlaylistGenerator
from ...domain.transcoding.constants import (
    MAX_RETRY_DELAY,
    MIN_STALL_TIMEOUT,
    RETRY_DELAY,
    STALL_TIMEOUT_PER_SEGMENT,
    TRANSIENT_INFINITE_RETRY,
)
from ...domain.transcoding.models import MediaInfo, QualityPreset, TranscodeProgress
from .adaptive import AdaptiveQualitySelector, HardwareProfiler, SystemProfile
from .commands import CommandBuilder
from .encoders import EncoderSelector
from .error_classifier import ErrorClassifier, FFmpegError
from .filters import FilterBuilder
from .ffmpeg_runner import FFmpegRunner
from .job_context import JobContext, JobRegistry, JobRegistryEntry
from .probe import MediaProbe

logger = logging.getLogger(__name__)


class TranscodeEngine:
    """
    FFmpeg-based transcoding engine with modular architecture — Specter-native (gevent).
    
    All async/await eliminated. Uses subprocess.Popen + gevent greenlets.
    """
    
    def __init__(
        self,
        *,
        config,
        capabilities,
        probe: MediaProbe,
        filter_builder: FilterBuilder,
        encoder_selector: EncoderSelector,
        command_builder: CommandBuilder,
        hardware_profiler: HardwareProfiler,
        error_classifier: ErrorClassifier,
        ffmpeg_runner: FFmpegRunner,
        job_registry: JobRegistry,
        hls_generator: HLSPlaylistGenerator,
    ):
        self.config = config
        self.capabilities = capabilities
        self.ffmpeg_path = self._find_ffmpeg()
        self.temp_dir = Path(self.config.transcoding.temp_directory)
        self.temp_dir.mkdir(parents=True, exist_ok=True)

        self.probe = probe
        self.filter_builder = filter_builder
        self.encoder_selector = encoder_selector
        self.command_builder = command_builder
        self.hardware_profiler = hardware_profiler
        self._hardware_profile: Optional[SystemProfile] = None
        
        # Concurrency control: semaphore to enforce max concurrent transcodes
        max_concurrent = self.config.transcoding.max_concurrent_jobs
        self._transcode_semaphore = gevent.lock.BoundedSemaphore(max_concurrent)
        
        # Optional job registry for tracking active/queued jobs
        self._job_registry = job_registry
        self._verbose_ffmpeg = os.environ.get('GHOSTSTREAM_FFMPEG_VERBOSE', '').lower() in ('1', 'true', 'yes')

        self._error_classifier = error_classifier
        self._ffmpeg_runner = ffmpeg_runner
        self._hls_generator = hls_generator
    
    @property
    def hardware_profile(self) -> SystemProfile:
        if self._hardware_profile is None:
            self._hardware_profile = self.hardware_profiler.get_profile()
        return self._hardware_profile
    
    def get_adaptive_quality_selector(self) -> AdaptiveQualitySelector:
        return AdaptiveQualitySelector(self.hardware_profile)
    
    def get_optimal_presets(self, media_info: MediaInfo) -> List[QualityPreset]:
        selector = self.get_adaptive_quality_selector()
        return selector.get_optimal_presets(media_info)
    
    def should_transcode(self, media_info: MediaInfo) -> Tuple[bool, str]:
        selector = self.get_adaptive_quality_selector()
        return selector.should_transcode(media_info)
    
    def _find_ffmpeg(self) -> str:
        if self.config.transcoding.ffmpeg_path != "auto":
            return self.config.transcoding.ffmpeg_path
        
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg:
            return ffmpeg
        
        raise RuntimeError("FFmpeg not found")
    
    def get_media_info(self, source: str, retry_count: int = 0) -> MediaInfo:
        """Get media information using ffprobe with retry logic."""
        return self.probe.get_media_info(source, retry_count)
    
    def build_hls_command(
        self,
        source: str,
        output_dir: Path,
        output_config: OutputConfig,
        start_time: float = 0,
        media_info: Optional[MediaInfo] = None,
        subtitles: Optional[List] = None
    ) -> Tuple[List[str], str]:
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
        return self.command_builder.build_abr_command(
            source, output_dir, output_config, media_info, start_time, variants, subtitles
        )
    
    def get_abr_variants(self, media_info: MediaInfo) -> List[QualityPreset]:
        return self.command_builder.get_abr_variants(media_info)
    
    def generate_master_playlist(
        self,
        output_dir: Path,
        variants: List[QualityPreset]
    ) -> str:
        return self.command_builder.generate_master_playlist(output_dir, variants)
    
    def _calculate_stall_timeout(self, media_info: MediaInfo) -> float:
        base_timeout = max(
            MIN_STALL_TIMEOUT,
            self.config.transcoding.stall_timeout
        )
        
        segment_duration = self.config.transcoding.segment_duration
        segment_factor = STALL_TIMEOUT_PER_SEGMENT * segment_duration
        
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
        grace = 30.0
        
        if media_info.width >= 3840:
            grace += 30.0
        elif media_info.width >= 1920:
            grace += 15.0
        
        if media_info.is_hdr:
            grace += 15.0
        
        return grace
    
    def _classify_error(self, error_msg: str) -> Tuple[Optional[FFmpegError], str]:
        return self._error_classifier.classify(error_msg)
    
    def _is_hardware_error(self, error_msg: str) -> bool:
        return self._error_classifier.is_hardware_error(error_msg)
    
    def _is_transient_error(self, error_msg: str) -> bool:
        return self._error_classifier.is_transient_error(error_msg)
    
    def _resolve_output_extension(self, output_format: OutputFormat) -> str:
        ext_map = {
            OutputFormat.MP4: ".mp4",
            OutputFormat.WEBM: ".webm",
            OutputFormat.MKV: ".mkv",
            OutputFormat.HLS: ".m3u8",
            OutputFormat.DASH: ".mpd",
        }
        return ext_map.get(output_format, ".mp4")
    
    def _check_file_growth(self, job_dir: Path, last_size: int) -> Tuple[int, bool]:
        try:
            total_size = 0
            for f in job_dir.glob("**/*"):
                if f.is_file():
                    total_size += f.stat().st_size
            return total_size, total_size > last_size
        except Exception:
            return last_size, False
    
    def _graceful_terminate(self, process: subprocess.Popen) -> None:
        """Gracefully terminate FFmpeg process with platform-specific signals."""
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
                logger.debug("[Transcode] FFmpeg terminated gracefully")
                return
            except gevent.Timeout:
                pass
            
            try:
                process.terminate()
                gevent.with_timeout(3.0, process.wait)
                logger.debug("[Transcode] FFmpeg terminated with SIGTERM")
                return
            except (gevent.Timeout, ProcessLookupError, OSError):
                pass
            
            try:
                process.kill()
                process.wait()
                logger.warning("[Transcode] FFmpeg killed forcefully")
            except (ProcessLookupError, OSError):
                pass
                
        except Exception as e:
            logger.warning(f"[Transcode] Error during process termination: {e}")
    
    def _run_ffmpeg(
        self,
        cmd: List[str],
        media_info: MediaInfo,
        progress_callback: Optional[Callable[[TranscodeProgress], None]],
        cancel_event: Optional[gevent.event.Event],
        stage: str = "transcoding",
        job_context: Optional[JobContext] = None
    ) -> Tuple[int, str]:
        """Run FFmpeg process with progress tracking via FFmpegRunner."""
        return self._ffmpeg_runner.run(
            cmd, media_info, progress_callback, cancel_event,
            stage=stage, job_context=job_context,
            segment_duration=self.config.transcoding.segment_duration
        )
    
    def _parse_progress(
        self,
        line: str,
        progress: TranscodeProgress,
        media_info: MediaInfo
    ) -> None:
        """Parse FFmpeg progress output."""
        match = re.search(r"frame=\s*(\d+)", line)
        if match:
            try:
                progress.frame = int(match.group(1))
            except (ValueError, TypeError):
                pass
        
        match = re.search(r"fps=\s*([\d.]+|N/A)", line)
        if match and match.group(1) != "N/A":
            try:
                progress.fps = float(match.group(1))
            except (ValueError, TypeError):
                pass
        
        match = re.search(r"bitrate=\s*([\d.]+\s*[kMG]?bits/s|N/A)", line)
        if match and match.group(1) != "N/A":
            progress.bitrate = match.group(1).strip()
        
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
        
        match = re.search(r"time=\s*(\d+):(\d+):(\d+\.?\d*)", line)
        if match:
            try:
                h, m, s = match.groups()
                progress.time = int(h) * 3600 + int(m) * 60 + float(s)
            except (ValueError, TypeError):
                pass
        else:
            match = re.search(r"time=\s*(\d+):(\d+\.?\d*)", line)
            if match:
                try:
                    m, s = match.groups()
                    progress.time = int(m) * 60 + float(s)
                except (ValueError, TypeError):
                    pass
        
        match = re.search(r"speed=\s*([\d.]+)x", line)
        if match:
            try:
                progress.speed = float(match.group(1))
            except (ValueError, TypeError):
                pass
        
        if media_info.duration > 0 and progress.time > 0:
            progress.percent = min(99.9, (progress.time / media_info.duration) * 100)
    
    def _prepare_job(self, job_id: str, source: str) -> Tuple[Optional[MediaInfo], Optional[Path], Optional[str]]:
        """Prepare job directory and get media info."""
        media_info = self.get_media_info(source)
        
        if media_info.duration <= 0:
            return None, None, f"Failed to get media info from: {source}. Check URL accessibility."
        
        MAX_DURATION = 48 * 3600
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
        if mode == TranscodeMode.STREAM:
            cmd, encoder_used = self.build_hls_command(
                source, job_dir, output_config, start_time, media_info, subtitles
            )
            output_path = str(job_dir / "master.m3u8")
        else:
            ext = self._resolve_output_extension(output_config.format)
            output_file = job_dir / f"output{ext}"
            
            cmd, encoder_used = self.build_batch_command(
                source, output_file, output_config, start_time, media_info
            )
            output_path = str(output_file)
        
        return cmd, encoder_used, output_path
    
    def _validate_hls_output(self, output_path: str, job_dir: Path) -> Tuple[bool, str]:
        """Validate HLS output."""
        master_path = Path(output_path)
        
        if not master_path.exists():
            return False, "Master playlist not found"
        
        content = master_path.read_text()
        if not content.strip():
            return False, "Master playlist is empty"
        
        has_variant = False
        segment_patterns = []
        
        for line in content.split("\n"):
            line = line.strip()
            if line.endswith(".m3u8") or line.endswith(".ts"):
                has_variant = True
                segment_patterns.append(line)
        
        if not has_variant:
            segment_files = list(job_dir.glob("*.ts"))
            if not segment_files:
                return False, "No variant playlists or segments found"
        
        segment_files = list(job_dir.glob("*.ts")) + list(job_dir.glob("*/*.ts"))
        if not segment_files:
            return False, "No segment files generated"
        
        first_segment = segment_files[0]
        if first_segment.stat().st_size == 0:
            return False, f"Segment {first_segment.name} is empty"
        
        seq_valid, seq_error = self._validate_segment_sequence(segment_files)
        if not seq_valid:
            return False, seq_error
        
        if self.config.transcoding.validate_segments:
            integrity_ok, integrity_msg = self._check_segment_integrity(segment_files)
            if not integrity_ok:
                return False, integrity_msg
        
        logger.debug(f"[Validate] HLS output valid: {len(segment_files)} segments")
        return True, ""
    
    def _validate_segment_sequence(self, segment_files: List[Path]) -> Tuple[bool, str]:
        if len(segment_files) < 2:
            return True, ""
        
        segment_numbers = []
        for f in segment_files:
            matches = re.findall(r"(\d+)", f.name)
            if matches:
                try:
                    segment_numbers.append(int(matches[-1]))
                except (ValueError, IndexError):
                    continue
        
        if len(segment_numbers) < 2:
            return True, ""
        
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
        if len(variants) < 2:
            return True, []
        
        warnings = []
        
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
        if not segment_files:
            return False, "No segments to check"
        
        min_segment_size = 1024
        ts_sync_byte = b'\x47'
        
        sizes = []
        for segment in segment_files[:10]:
            try:
                size = segment.stat().st_size
                sizes.append(size)
                
                if size < min_segment_size:
                    return False, f"Segment {segment.name} too small: {size} bytes"
                
                with open(segment, 'rb') as f:
                    header = f.read(4)
                    if not header or header[0:1] != ts_sync_byte:
                        return False, f"Segment {segment.name} missing MPEG-TS sync byte"
                        
            except Exception as e:
                return False, f"Error checking segment {segment.name}: {e}"
        
        if len(sizes) >= 3:
            avg_size = sum(sizes) / len(sizes)
            for i, size in enumerate(sizes[:-1]):
                if size < avg_size * 0.05:
                    return False, f"Segment {segment_files[i].name} suspiciously small ({size} vs avg {avg_size:.0f})"
        
        return True, ""
    
    def _validate_batch_output(self, output_path: str) -> Tuple[bool, str]:
        path = Path(output_path)
        
        if not path.exists():
            return False, "Output file not found"
        
        size = path.stat().st_size
        if size == 0:
            return False, "Output file is empty"
        
        if size < 1024:
            return False, f"Output file suspiciously small: {size} bytes"
        
        return True, ""
    
    def _validate_output(
        self,
        mode: TranscodeMode,
        output_path: str,
        job_dir: Path
    ) -> Tuple[bool, str]:
        if mode == TranscodeMode.STREAM:
            return self._validate_hls_output(output_path, job_dir)
        else:
            return self._validate_batch_output(output_path)
    
    def _execute_with_retry(
        self,
        cmd: List[str],
        encoder_used: str,
        output_path: str,
        mode: TranscodeMode,
        job_context: JobContext,
        media_info: MediaInfo,
        current_config: OutputConfig,
        progress_callback: Optional[Callable[[TranscodeProgress], None]],
        cancel_event: Optional[gevent.event.Event]
    ) -> Tuple[bool, str, Optional[str]]:
        """Execute FFmpeg with retry logic and per-job hardware fallback."""
        log_prefix = job_context.log_prefix
        retry_count = self.config.transcoding.retry_count
        source = job_context.source
        job_dir = job_context.job_dir
        
        for attempt in range(retry_count + 1):
            if cancel_event and cancel_event.is_set():
                return False, "Cancelled", None
            
            logger.info(f"{log_prefix} Attempt {attempt + 1}/{retry_count + 1} with encoder: {encoder_used}")
            
            return_code, error_output = self._run_ffmpeg(
                cmd, media_info, progress_callback, cancel_event,
                job_context=job_context
            )
            
            if cancel_event and cancel_event.is_set():
                return False, "Cancelled", encoder_used
            
            if return_code == 0:
                is_valid, validation_error = self._validate_output(mode, output_path, job_dir)
                
                if is_valid:
                    hw_accel_used = self.encoder_selector.detect_hw_accel_used(encoder_used)
                    logger.info(f"{log_prefix} Complete. HW accel: {hw_accel_used}")
                    
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
            
            error_info, error_category = self._classify_error(error_msg)
            
            if not job_context.hw_fallback_attempted and error_category == "hardware":
                logger.info(f"{log_prefix} Hardware error detected, falling back to software")
                current_config.hw_accel = HWAccel.SOFTWARE
                job_context.hw_fallback_attempted = True
                
                self.encoder_selector.mark_hw_failed(encoder_used)
                
                self._cleanup_dir(job_dir)
                
                cmd, encoder_used, output_path = self._build_transcode_command(
                    mode, source, job_dir, current_config, 0, media_info
                )
                continue
            
            if self._is_transient_error(error_msg):
                import random
                base_delay = min(RETRY_DELAY * (2 ** attempt), MAX_RETRY_DELAY)
                jitter = random.uniform(0, base_delay * 0.1)
                delay = base_delay + jitter
                desc = error_info.description if error_info else "Transient error"
                
                if TRANSIENT_INFINITE_RETRY or attempt < retry_count:
                    logger.info(f"{log_prefix} {desc}, retrying in {delay:.1f}s (attempt {attempt + 1})...")
                    gevent.sleep(delay)
                    if TRANSIENT_INFINITE_RETRY:
                        self._cleanup_dir(job_dir)
                        continue
                    continue
            
            return False, f"FFmpeg error: {error_msg}", encoder_used
        
        return False, "Max retries exceeded", None
    
    def _cleanup_dir(self, dir_path: Path) -> None:
        """Clean directory contents."""
        for f in dir_path.glob("*"):
            try:
                if f.is_file():
                    f.unlink()
                elif f.is_dir():
                    shutil.rmtree(f, ignore_errors=True)
            except Exception as e:
                logger.debug(f"Failed to clean {f}: {e}")
    
    def transcode(
        self,
        job_id: str,
        source: str,
        mode: TranscodeMode,
        output_config: OutputConfig,
        start_time: float = 0,
        progress_callback: Optional[Callable[[TranscodeProgress], None]] = None,
        cancel_event: Optional[gevent.event.Event] = None,
        subtitles: Optional[List] = None
    ) -> Tuple[bool, str, Optional[str]]:
        """
        Execute transcoding with retry logic and hardware fallback.
        
        Uses semaphore to enforce max concurrent transcodes.
        """
        log_prefix = f"[Job:{job_id[:8]}]"
        job_dir: Optional[Path] = None
        
        # Register job
        self._job_registry.register(job_id, source)
        
        # Acquire semaphore to enforce max concurrent transcodes
        self._transcode_semaphore.acquire()
        self._job_registry.update_status(job_id, "running")
        
        try:
            media_info, job_dir, error = self._prepare_job(job_id, source)
            if error:
                self._job_registry.update_status(job_id, "failed")
                return False, error, None
            
            job_context = JobContext(
                job_id=job_id,
                source=source,
                job_dir=job_dir
            )
            
            current_config = OutputConfig(**output_config.model_dump())
            
            cmd, encoder_used, output_path = self._build_transcode_command(
                mode, source, job_dir, current_config, start_time, media_info, subtitles
            )
            
            self._job_registry.update_status(job_id, "running", encoder=encoder_used)
            
            success, result, hw_accel = self._execute_with_retry(
                cmd, encoder_used, output_path, mode, job_context, media_info,
                current_config, progress_callback, cancel_event
            )
            
            final_status = "completed" if success else "failed"
            self._job_registry.update_status(job_id, final_status, progress=100.0 if success else 0.0)
            
            return success, result, hw_accel
            
        except gevent.GreenletExit:
            self._job_registry.update_status(job_id, "cancelled")
            if job_dir and job_dir.exists():
                self._cleanup_dir(job_dir)
            return False, "Cancelled", None
            
        except Exception as e:
            logger.exception(f"{log_prefix} Unexpected error: {e}")
            self._job_registry.update_status(job_id, "failed")
            if job_dir and job_dir.exists():
                self._cleanup_dir(job_dir)
            return False, str(e), None
        
        finally:
            self._transcode_semaphore.release()
            self._job_registry.remove(job_id)
    
    def transcode_abr(
        self,
        job_id: str,
        source: str,
        output_config: OutputConfig,
        start_time: float = 0,
        progress_callback: Optional[Callable[[TranscodeProgress], None]] = None,
        cancel_event: Optional[gevent.event.Event] = None,
        subtitles: Optional[List] = None
    ) -> Tuple[bool, str, Optional[str]]:
        """Execute ABR transcoding with multiple quality variants."""
        log_prefix = f"[Job:{job_id[:8]}]"
        job_dir: Optional[Path] = None
        
        self._job_registry.register(job_id, source)
        
        self._transcode_semaphore.acquire()
        self._job_registry.update_status(job_id, "running")
        
        try:
            media_info = self.get_media_info(source)
            if media_info.duration == 0:
                self._job_registry.update_status(job_id, "failed")
                return False, f"Failed to get media info from: {source}", None
            
            job_dir = self.temp_dir / job_id
            job_dir.mkdir(parents=True, exist_ok=True)
            
            job_context = JobContext(
                job_id=job_id,
                source=source,
                job_dir=job_dir
            )
            
            current_config = OutputConfig(**output_config.model_dump())
            
            variants = self.get_optimal_presets(media_info)
            
            cmd, encoder_used, variants = self.build_abr_command(
                source, job_dir, current_config, media_info, start_time, variants, subtitles
            )
            
            spacing_ok, spacing_warnings = self._validate_hls_bitrate_spacing(variants)
            if not spacing_ok:
                for warning in spacing_warnings:
                    logger.warning(f"{log_prefix} {warning}")
            
            self._job_registry.update_status(job_id, "running", encoder=encoder_used)
            logger.info(f"{log_prefix} Starting ABR transcode with {len(variants)} variants")
            
            return_code, error_output = self._run_ffmpeg(
                cmd, media_info, progress_callback, cancel_event,
                job_context=job_context
            )
            
            if cancel_event and cancel_event.is_set():
                self._job_registry.update_status(job_id, "cancelled")
                return False, "Cancelled", encoder_used
            
            if return_code == 0:
                master_path = job_dir / "master.m3u8"
                if master_path.exists():
                    hw_accel = self.encoder_selector.detect_hw_accel_used(encoder_used)
                    logger.info(f"{log_prefix} ABR complete with {len(variants)} variants")
                    self._job_registry.update_status(job_id, "completed", progress=100.0)
                    
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
            
            error_msg = error_output[-1000:] if error_output else "Unknown error"
            logger.warning(f"{log_prefix} ABR failed (code {return_code}): {error_msg[:200]}...")
            
            logger.warning(f"{log_prefix} ABR failed, falling back to single quality")
            self._job_registry.remove(job_id)
            
            return self.transcode(
                job_id, source, TranscodeMode.STREAM, output_config,
                start_time, progress_callback, cancel_event
            )
            
        except gevent.GreenletExit:
            self._job_registry.update_status(job_id, "cancelled")
            if job_dir and job_dir.exists():
                self._cleanup_dir(job_dir)
            return False, "Cancelled", None
            
        except Exception as e:
            logger.exception(f"{log_prefix} ABR error: {e}")
            self._job_registry.update_status(job_id, "failed")
            if job_dir and job_dir.exists():
                self._cleanup_dir(job_dir)
            return False, str(e), None
        
        finally:
            self._transcode_semaphore.release()
            self._job_registry.remove(job_id)
    
    def cleanup_job(self, job_id: str) -> None:
        """Clean up job files."""
        job_dir = self.temp_dir / job_id
        if job_dir.exists():
            try:
                shutil.rmtree(job_dir, ignore_errors=True)
                logger.info(f"Cleaned up job directory: {job_dir}")
            except Exception as e:
                logger.warning(f"Failed to cleanup job {job_id}: {e}")
    
    @property
    def max_concurrent_jobs(self) -> int:
        return self.config.transcoding.max_concurrent_jobs

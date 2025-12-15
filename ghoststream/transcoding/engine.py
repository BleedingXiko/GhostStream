"""
Main transcoding engine that orchestrates all transcoding operations.
"""

import asyncio
import re
import shutil
import time
import logging
from pathlib import Path
from typing import Optional, Callable, List, Tuple

from ..models import OutputConfig, OutputFormat, VideoCodec, HWAccel, TranscodeMode
from ..hardware import get_capabilities
from ..config import get_config
from .models import MediaInfo, TranscodeProgress, QualityPreset
from .constants import MAX_RETRIES, RETRY_DELAY
from .filters import FilterBuilder
from .encoders import EncoderSelector
from .probe import MediaProbe
from .commands import CommandBuilder
from .adaptive import HardwareProfiler, AdaptiveQualitySelector, SystemProfile

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
    
    async def _run_ffmpeg(
        self,
        cmd: List[str],
        media_info: MediaInfo,
        progress_callback: Optional[Callable[[TranscodeProgress], None]],
        cancel_event: Optional[asyncio.Event],
        stage: str = "transcoding"
    ) -> Tuple[int, str]:
        """Run FFmpeg process with progress tracking. Returns (return_code, error_output)."""
        
        logger.info(f"[Transcode] Running FFmpeg: {' '.join(cmd[:10])}...")
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        progress = TranscodeProgress(stage=stage)
        stderr_lines: List[str] = []
        last_progress_time = time.time()
        stall_timeout = 120
        
        async def read_stderr():
            nonlocal last_progress_time
            while True:
                if cancel_event and cancel_event.is_set():
                    try:
                        process.terminate()
                        await asyncio.sleep(0.5)
                        if process.returncode is None:
                            process.kill()
                    except:
                        pass
                    return
                
                try:
                    line = await asyncio.wait_for(process.stderr.readline(), timeout=1.0)
                except asyncio.TimeoutError:
                    if time.time() - last_progress_time > stall_timeout:
                        logger.error("[Transcode] FFmpeg appears stalled, terminating")
                        try:
                            process.terminate()
                        except:
                            pass
                        return
                    continue
                
                if not line:
                    break
                
                line_str = line.decode("utf-8", errors="ignore")
                stderr_lines.append(line_str)
                
                if len(stderr_lines) > 100:
                    stderr_lines.pop(0)
                
                if "frame=" in line_str:
                    last_progress_time = time.time()
                    self._parse_progress(line_str, progress, media_info)
                    
                    if progress_callback:
                        try:
                            progress_callback(progress)
                        except Exception as e:
                            logger.warning(f"Progress callback error: {e}")
        
        await read_stderr()
        
        try:
            await asyncio.wait_for(process.wait(), timeout=30.0)
        except asyncio.TimeoutError:
            logger.error("[Transcode] FFmpeg did not exit cleanly, killing")
            try:
                process.kill()
                await process.wait()
            except:
                pass
        
        error_output = "".join(stderr_lines)
        return process.returncode or 0, error_output
    
    def _parse_progress(
        self,
        line: str,
        progress: TranscodeProgress,
        media_info: MediaInfo
    ) -> None:
        """Parse FFmpeg progress output."""
        match = re.search(r"frame=\s*(\d+)", line)
        if match:
            progress.frame = int(match.group(1))
        
        match = re.search(r"fps=\s*([\d.]+)", line)
        if match:
            progress.fps = float(match.group(1))
        
        match = re.search(r"bitrate=\s*([\d.]+\s*\w+)", line)
        if match:
            progress.bitrate = match.group(1)
        
        match = re.search(r"size=\s*(\d+)", line)
        if match:
            progress.total_size = int(match.group(1)) * 1024
        
        match = re.search(r"time=(\d+):(\d+):(\d+\.?\d*)", line)
        if match:
            h, m, s = match.groups()
            progress.time = int(h) * 3600 + int(m) * 60 + float(s)
        
        match = re.search(r"speed=\s*([\d.]+)x", line)
        if match:
            progress.speed = float(match.group(1))
        
        if media_info.duration > 0:
            progress.percent = min(99.9, (progress.time / media_info.duration) * 100)
    
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
        media_info = await self.get_media_info(source)
        if media_info.duration == 0:
            return False, f"Failed to get media info from: {source}. Check URL accessibility.", None
        
        job_dir = self.temp_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        
        current_config = OutputConfig(**output_config.model_dump())
        fallback_attempted = False
        
        for attempt in range(MAX_RETRIES + 1):
            if cancel_event and cancel_event.is_set():
                return False, "Cancelled", None
            
            try:
                if mode == TranscodeMode.STREAM:
                    cmd, encoder_used = self.build_hls_command(
                        source, job_dir, current_config, start_time, media_info
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
                    ext = ext_map.get(current_config.format, ".mp4")
                    output_file = job_dir / f"output{ext}"
                    
                    cmd, encoder_used = self.build_batch_command(
                        source, output_file, current_config, start_time, media_info
                    )
                    output_path = str(output_file)
                
                logger.info(f"[Transcode] Attempt {attempt + 1}/{MAX_RETRIES + 1} with encoder: {encoder_used}")
                
                return_code, error_output = await self._run_ffmpeg(
                    cmd, media_info, progress_callback, cancel_event
                )
                
                if cancel_event and cancel_event.is_set():
                    return False, "Cancelled", encoder_used
                
                if return_code == 0:
                    if mode == TranscodeMode.STREAM:
                        if Path(output_path).exists():
                            hw_accel_used = self.encoder_selector.detect_hw_accel_used(encoder_used)
                            logger.info(f"[Transcode] Complete. HW accel: {hw_accel_used}")
                            return True, output_path, hw_accel_used
                    else:
                        if Path(output_path).exists() and Path(output_path).stat().st_size > 0:
                            hw_accel_used = self.encoder_selector.detect_hw_accel_used(encoder_used)
                            logger.info(f"[Transcode] Complete. HW accel: {hw_accel_used}")
                            return True, output_path, hw_accel_used
                    
                    logger.warning("[Transcode] FFmpeg returned success but output missing/empty")
                
                error_msg = error_output[-1000:] if error_output else "Unknown error"
                logger.warning(f"[Transcode] FFmpeg failed (code {return_code}): {error_msg[:200]}")
                
                if not fallback_attempted and self.encoder_selector.is_hw_error(error_msg):
                    logger.info("[Transcode] Hardware error detected, falling back to software")
                    current_config.hw_accel = HWAccel.SOFTWARE
                    fallback_attempted = True
                    for f in job_dir.glob("*"):
                        try:
                            f.unlink()
                        except:
                            pass
                    continue
                
                if attempt < MAX_RETRIES and any(err in error_msg.lower() for err in
                    ["connection", "timeout", "refused", "temporary", "resource"]):
                    logger.info(f"[Transcode] Transient error, retrying in {RETRY_DELAY}s...")
                    await asyncio.sleep(RETRY_DELAY * (attempt + 1))
                    continue
                
                return False, f"FFmpeg error: {error_msg}", encoder_used
                
            except asyncio.CancelledError:
                return False, "Cancelled", None
            except Exception as e:
                logger.exception(f"[Transcode] Unexpected error: {e}")
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(RETRY_DELAY)
                    continue
                return False, str(e), None
        
        return False, "Max retries exceeded", None
    
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
        """Clean up job files."""
        job_dir = self.temp_dir / job_id
        if job_dir.exists():
            try:
                shutil.rmtree(job_dir, ignore_errors=True)
                logger.info(f"Cleaned up job directory: {job_dir}")
            except Exception as e:
                logger.warning(f"Failed to cleanup job {job_id}: {e}")

"""
Media probing using ffprobe.
Extracts media information including HDR detection.
"""

import asyncio
import json
import logging
import shutil
from typing import Optional

from .models import MediaInfo
from .constants import MAX_RETRIES, RETRY_DELAY

logger = logging.getLogger(__name__)


class MediaProbe:
    """Probes media files to extract information."""
    
    def __init__(self, ffprobe_path: str = "auto"):
        self.ffprobe_path = self._find_ffprobe(ffprobe_path)
    
    def _find_ffprobe(self, path: str) -> str:
        """Find ffprobe executable."""
        if path != "auto":
            return path
        
        ffprobe = shutil.which("ffprobe")
        if ffprobe:
            return ffprobe
        return "ffprobe"
    
    async def get_media_info(self, source: str, retry_count: int = 0) -> MediaInfo:
        """Get media information using ffprobe with retry logic."""
        logger.info(f"[Probe] Getting media info for source: {source}")
        
        cmd = [self.ffprobe_path]
        
        # Add protocol options for HTTP sources
        # Use smaller analyze/probe sizes for faster probing over slow networks (e.g. Pi)
        if source.startswith('http://') or source.startswith('https://'):
            cmd.extend([
                "-headers", "User-Agent: GhostStream/1.0\r\n",
                "-timeout", "60000000",  # 60 second timeout in microseconds
                "-analyzeduration", "10M",  # 10MB - enough for most files, much faster
                "-probesize", "10M",  # 10MB - reduces initial read significantly
                "-fflags", "+genpts",  # Generate PTS if missing
            ])
        
        cmd.extend([
            "-v", "error",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            source
        ])
        
        logger.debug(f"[Probe] ffprobe command: {' '.join(cmd)}")
        
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=60.0  # 60 second timeout
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                if retry_count < MAX_RETRIES:
                    logger.warning(f"[Probe] ffprobe timeout, retrying ({retry_count + 1}/{MAX_RETRIES})")
                    await asyncio.sleep(RETRY_DELAY)
                    return await self.get_media_info(source, retry_count + 1)
                logger.error(f"[Probe] ffprobe timed out for '{source}'")
                return MediaInfo()
            
            if process.returncode != 0:
                stderr_text = stderr.decode().strip()
                # Retry on transient errors
                if retry_count < MAX_RETRIES and any(err in stderr_text.lower() for err in
                    ["connection", "timeout", "refused", "reset", "temporary"]):
                    logger.warning(f"[Probe] ffprobe failed (transient), retrying ({retry_count + 1}/{MAX_RETRIES})")
                    await asyncio.sleep(RETRY_DELAY)
                    return await self.get_media_info(source, retry_count + 1)
                
                logger.error(f"[Probe] ffprobe failed for '{source}'")
                logger.error(f"[Probe] stderr: {stderr_text}")
                logger.error(f"[Probe] Check: 1) Is GhostHub running? 2) Is the IP correct? 3) Firewall blocking?")
                return MediaInfo()
            
            data = json.loads(stdout.decode())
            info = self._parse_probe_data(data)
            
            logger.info(f"[Probe] Media info: {info.width}x{info.height}, {info.video_codec}, "
                       f"HDR={info.is_hdr}, 10bit={info.is_10bit}, duration={info.duration:.1f}s")
            return info
            
        except json.JSONDecodeError as e:
            logger.error(f"[Probe] Failed to parse ffprobe JSON: {e}")
            return MediaInfo()
        except Exception as e:
            logger.error(f"Failed to get media info: {e}")
            if retry_count < MAX_RETRIES:
                logger.warning(f"[Probe] Retrying media info ({retry_count + 1}/{MAX_RETRIES})")
                await asyncio.sleep(RETRY_DELAY)
                return await self.get_media_info(source, retry_count + 1)
            return MediaInfo()
    
    def _parse_probe_data(self, data: dict) -> MediaInfo:
        """Parse ffprobe JSON output into MediaInfo."""
        info = MediaInfo()
        
        # Get duration from format
        if "format" in data:
            info.duration = float(data["format"].get("duration", 0))
            info.bitrate = int(data["format"].get("bit_rate", 0))
        
        # Get stream info
        for stream in data.get("streams", []):
            if stream.get("codec_type") == "video" and not info.video_codec:
                info.video_codec = stream.get("codec_name", "")
                info.width = stream.get("width", 0)
                info.height = stream.get("height", 0)
                info.pixel_format = stream.get("pix_fmt", "")
                info.color_transfer = stream.get("color_transfer", "")
                info.color_primaries = stream.get("color_primaries", "")
                
                # Parse frame rate
                fps_str = stream.get("r_frame_rate", "0/1")
                if "/" in fps_str:
                    num, den = fps_str.split("/")
                    info.fps = float(num) / float(den) if float(den) > 0 else 0
                
                # Detect 10-bit content
                pix_fmt = info.pixel_format.lower()
                info.is_10bit = any(x in pix_fmt for x in ["10le", "10be", "p010", "yuv420p10"])
                
                # Detect HDR
                hdr_transfers = ["smpte2084", "arib-std-b67", "bt2020-10", "bt2020-12"]
                info.is_hdr = (
                    info.color_transfer.lower() in hdr_transfers or
                    info.color_primaries.lower() == "bt2020" or
                    (info.is_10bit and info.video_codec.lower() in ["hevc", "h265", "av1", "vp9"])
                )
                
                # Check for B-frames (important for seeking)
                info.has_bframes = stream.get("has_b_frames", 0) > 0
                
            elif stream.get("codec_type") == "audio" and not info.audio_codec:
                info.audio_codec = stream.get("codec_name", "")
                info.audio_channels = stream.get("channels", 2)
                info.audio_sample_rate = int(stream.get("sample_rate", 48000))
        
        return info

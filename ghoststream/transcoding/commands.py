"""
FFmpeg command building for various output formats.
Handles HLS, batch, and ABR transcoding commands.
"""

import os
import logging
from pathlib import Path
from typing import List, Tuple, Optional

from ..models import OutputConfig, OutputFormat, VideoCodec, Resolution
from ..config import TranscodingConfig, HardwareConfig
from .models import MediaInfo, QualityPreset
from .constants import get_bitrate_map, AUDIO_BITRATE_MAP, QUALITY_LADDER
from .filters import FilterBuilder
from .encoders import EncoderSelector

logger = logging.getLogger(__name__)


class CommandBuilder:
    """Builds FFmpeg commands for transcoding operations."""
    
    def __init__(
        self,
        ffmpeg_path: str,
        encoder_selector: EncoderSelector,
        filter_builder: FilterBuilder,
        transcoding_config: TranscodingConfig,
        hw_config: HardwareConfig
    ):
        self.ffmpeg_path = ffmpeg_path
        self.encoder_selector = encoder_selector
        self.filter_builder = filter_builder
        self.transcoding_config = transcoding_config
        self.hw_config = hw_config
    
    def _parse_bitrate(self, bitrate: str) -> tuple:
        """Parse bitrate string into (value, unit). Returns (float_value, 'M' or 'k')."""
        bitrate = bitrate.strip()
        if bitrate.upper().endswith('M'):
            return float(bitrate[:-1]), 'M'
        elif bitrate.upper().endswith('K'):
            return float(bitrate[:-1]), 'k'
        else:
            return float(bitrate), 'M'
    
    def _get_bufsize(self, bitrate: str) -> str:
        """Calculate bufsize (2x bitrate) preserving the unit."""
        value, unit = self._parse_bitrate(bitrate)
        return f"{int(value * 2)}{unit}"
    
    def _get_bandwidth_bps(self, bitrate: str) -> int:
        """Convert bitrate string to bits per second for HLS playlist."""
        value, unit = self._parse_bitrate(bitrate)
        if unit == 'M':
            return int(value * 1_000_000)
        else:  # 'k'
            return int(value * 1_000)
    
    def _get_bitrate(self, resolution: Resolution, bitrate: str) -> Optional[str]:
        """Get the target bitrate."""
        if bitrate != "auto":
            return bitrate
        return get_bitrate_map().get(resolution)
    
    def _get_protocol_args(self, source: str) -> List[str]:
        """Get protocol options for HTTP sources."""
        if source.startswith('http://') or source.startswith('https://'):
            return [
                "-headers", "User-Agent: GhostStream/1.0\r\n",
                "-reconnect", "1",
                "-reconnect_streamed", "1",
                "-reconnect_delay_max", "5",
                "-timeout", "30000000",
            ]
        return []
    
    def build_hls_command(
        self,
        source: str,
        output_dir: Path,
        output_config: OutputConfig,
        start_time: float = 0,
        media_info: Optional[MediaInfo] = None
    ) -> Tuple[List[str], str]:
        """Build FFmpeg command for HLS output."""
        
        video_encoder, video_args = self.encoder_selector.get_video_encoder(
            output_config.video_codec,
            output_config.hw_accel
        )
        audio_encoder, audio_args = self.encoder_selector.get_audio_encoder(
            output_config.audio_codec
        )
        
        cmd = [self.ffmpeg_path, "-y", "-hide_banner"]
        
        # Protocol options for HTTP sources
        cmd.extend(self._get_protocol_args(source))
        
        # Hardware decoding (only if not doing HDR tonemap which requires CPU filters)
        needs_cpu_filters = self.filter_builder.needs_tonemap(media_info, output_config)
        if not needs_cpu_filters:
            hw_args, hw_type = self.encoder_selector.get_hw_decode_args(
                video_encoder, self.hw_config.vaapi_device
            )
            cmd.extend(hw_args)
        
        # Start time (before input for faster seeking)
        if start_time > 0:
            cmd.extend(["-ss", str(start_time)])
        
        # Input
        cmd.extend(["-i", source])
        
        # Map streams explicitly
        cmd.extend(["-map", "0:v:0", "-map", "0:a:0?"])
        
        # Video encoding
        cmd.extend(["-c:v", video_encoder])
        cmd.extend(video_args)
        
        # Build and apply video filters
        vf_filters = self.filter_builder.build_video_filters(
            media_info, output_config, video_encoder
        )
        # Ensure compatible pixel format for software encoders
        if vf_filters and "lib" in video_encoder:
            vf_filters.append("format=yuv420p")
        if vf_filters:
            cmd.extend(["-vf", ",".join(vf_filters)])
        
        # Video bitrate with maxrate/bufsize for consistent streaming
        bitrate = self._get_bitrate(output_config.resolution, output_config.bitrate)
        if bitrate and video_encoder != "copy":
            cmd.extend(["-b:v", bitrate])
            # Add maxrate and bufsize for better streaming
            cmd.extend(["-maxrate", bitrate, "-bufsize", self._get_bufsize(bitrate)])
        
        # Keyframe interval for seeking (every 2 seconds)
        if video_encoder != "copy":
            gop_size = int((media_info.fps if media_info else 30) * 2)
            cmd.extend(["-g", str(gop_size), "-keyint_min", str(gop_size)])
        
        # Audio encoding with proper channel handling
        cmd.extend(["-c:a", audio_encoder])
        if audio_encoder != "copy":
            channels = media_info.audio_channels if media_info else 2
            audio_br = AUDIO_BITRATE_MAP.get(channels, "128k")
            cmd.extend(["-b:a", audio_br, "-ac", str(min(channels, 2))])
        
        # HLS specific options
        segment_duration = self.transcoding_config.segment_duration
        playlist_path = output_dir / "master.m3u8"
        segment_pattern = output_dir / "segment_%05d.ts"
        
        cmd.extend([
            "-f", "hls",
            "-hls_time", str(segment_duration),
            "-hls_list_size", "0",
            "-hls_segment_filename", str(segment_pattern),
            "-hls_flags", "independent_segments+append_list",
            "-hls_segment_type", "mpegts",
            "-hls_playlist_type", "vod",
            str(playlist_path)
        ])
        
        return cmd, video_encoder
    
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
        """Build FFmpeg command for batch transcoding with optional two-pass."""
        
        video_encoder, video_args = self.encoder_selector.get_video_encoder(
            output_config.video_codec,
            output_config.hw_accel
        )
        audio_encoder, audio_args = self.encoder_selector.get_audio_encoder(
            output_config.audio_codec
        )
        
        cmd = [self.ffmpeg_path, "-y", "-hide_banner"]
        
        # Protocol options for HTTP sources
        cmd.extend(self._get_protocol_args(source))
        
        # Hardware decoding (only if not doing HDR tonemap)
        needs_cpu_filters = self.filter_builder.needs_tonemap(media_info, output_config)
        if not needs_cpu_filters:
            hw_args, _ = self.encoder_selector.get_hw_decode_args(
                video_encoder, self.hw_config.vaapi_device
            )
            cmd.extend(hw_args)
        
        # Start time
        if start_time > 0:
            cmd.extend(["-ss", str(start_time)])
        
        # Input
        cmd.extend(["-i", source])
        
        # Map streams explicitly
        cmd.extend(["-map", "0:v:0", "-map", "0:a:0?"])
        
        # Video encoding
        cmd.extend(["-c:v", video_encoder])
        cmd.extend(video_args)
        
        # Two-pass encoding settings
        if two_pass and "lib" in video_encoder:
            cmd.extend(["-pass", str(pass_num)])
            if passlog_prefix:
                cmd.extend(["-passlogfile", passlog_prefix])
        
        # Build and apply video filters
        vf_filters = self.filter_builder.build_video_filters(
            media_info, output_config, video_encoder
        )
        # Ensure compatible pixel format for software encoders
        if vf_filters and "lib" in video_encoder:
            vf_filters.append("format=yuv420p")
        if vf_filters:
            cmd.extend(["-vf", ",".join(vf_filters)])
        
        # Video bitrate
        bitrate = self._get_bitrate(output_config.resolution, output_config.bitrate)
        if bitrate and video_encoder != "copy":
            cmd.extend(["-b:v", bitrate])
        
        # Audio encoding (skip on first pass of two-pass)
        if two_pass and pass_num == 1:
            cmd.extend(["-an"])
        else:
            cmd.extend(["-c:a", audio_encoder])
            if audio_encoder != "copy":
                channels = media_info.audio_channels if media_info else 2
                audio_br = AUDIO_BITRATE_MAP.get(channels, "128k")
                cmd.extend(["-b:a", audio_br])
        
        # Output format specific options
        if output_config.format == OutputFormat.MP4:
            cmd.extend(["-movflags", "+faststart"])
        elif output_config.format == OutputFormat.WEBM:
            cmd.extend(["-f", "webm"])
        elif output_config.format == OutputFormat.MKV:
            cmd.extend(["-f", "matroska"])
        
        # First pass outputs to null
        if two_pass and pass_num == 1:
            if os.name == 'nt':
                cmd.extend(["-f", "null", "NUL"])
            else:
                cmd.extend(["-f", "null", "/dev/null"])
        else:
            cmd.append(str(output_path))
        
        return cmd, video_encoder
    
    def get_abr_variants(self, media_info: MediaInfo) -> List[QualityPreset]:
        """Get appropriate ABR variants based on source resolution."""
        variants = []
        source_height = media_info.height
        
        for preset in QUALITY_LADDER:
            if preset.height <= source_height:
                variants.append(preset)
        
        if not variants and QUALITY_LADDER:
            variants.append(QUALITY_LADDER[-1])
        
        return variants[:4]
    
    def build_abr_command(
        self,
        source: str,
        output_dir: Path,
        output_config: OutputConfig,
        media_info: MediaInfo,
        start_time: float = 0
    ) -> Tuple[List[str], str, List[QualityPreset]]:
        """Build FFmpeg command for ABR HLS with multiple quality variants."""
        
        video_encoder, video_args = self.encoder_selector.get_video_encoder(
            output_config.video_codec,
            output_config.hw_accel
        )
        audio_encoder, _ = self.encoder_selector.get_audio_encoder(output_config.audio_codec)
        
        variants = self.get_abr_variants(media_info)
        
        cmd = [self.ffmpeg_path, "-y", "-hide_banner"]
        
        # Protocol options for HTTP sources
        cmd.extend(self._get_protocol_args(source))
        
        # Hardware decoding (skip if HDR tonemap needed)
        needs_cpu_filters = self.filter_builder.needs_tonemap(media_info, output_config)
        if not needs_cpu_filters:
            hw_args, _ = self.encoder_selector.get_hw_decode_args(
                video_encoder, self.hw_config.vaapi_device
            )
            cmd.extend(hw_args)
        
        # Start time
        if start_time > 0:
            cmd.extend(["-ss", str(start_time)])
        
        # Input
        cmd.extend(["-i", source])
        
        # Build filter complex for multiple outputs
        filter_parts = self.filter_builder.build_abr_filter_complex(
            variants, media_info, needs_cpu_filters, video_encoder
        )
        
        map_args = []
        stream_maps = []
        
        for i, variant in enumerate(variants):
            # Map video output
            map_args.extend(["-map", f"[v{i}]"])
            
            # Video encoding for this variant
            map_args.extend([f"-c:v:{i}", video_encoder])
            map_args.extend([f"-b:v:{i}", variant.video_bitrate])
            map_args.extend([f"-maxrate:v:{i}", variant.video_bitrate])
            map_args.extend([f"-bufsize:v:{i}", self._get_bufsize(variant.video_bitrate)])
            
            # Add preset for this variant (don't use CRF with ABR - bitrate mode only)
            if "nvenc" in video_encoder:
                map_args.extend([f"-preset:v:{i}", variant.hw_preset])
            elif "libx264" in video_encoder:
                map_args.extend([f"-preset:v:{i}", "medium"])
            elif "libx265" in video_encoder:
                map_args.extend([f"-preset:v:{i}", "medium"])
            
            # Keyframe interval
            gop = int(media_info.fps * 2) if media_info.fps > 0 else 60
            map_args.extend([f"-g:v:{i}", str(gop)])
            
            stream_maps.append(f"v:{i},a:0")
        
        # Apply filter complex
        if filter_parts:
            cmd.extend(["-filter_complex", ";".join(filter_parts)])
        
        cmd.extend(map_args)
        
        # Single audio stream (shared across variants)
        cmd.extend(["-map", "0:a:0?"])
        cmd.extend(["-c:a", audio_encoder])
        if audio_encoder != "copy":
            cmd.extend(["-b:a", "128k", "-ac", "2"])
        
        # HLS options
        segment_duration = self.transcoding_config.segment_duration
        
        # Use forward slashes for FFmpeg paths (works on all platforms)
        segment_path = str(output_dir / "stream_%v_%05d.ts").replace("\\", "/")
        playlist_path = str(output_dir / "stream_%v.m3u8").replace("\\", "/")
        
        cmd.extend([
            "-f", "hls",
            "-hls_time", str(segment_duration),
            "-hls_list_size", "0",
            "-hls_flags", "independent_segments+append_list",
            "-hls_segment_type", "mpegts",
            "-hls_playlist_type", "vod",
            "-master_pl_name", "master.m3u8",
            "-hls_segment_filename", segment_path,
            "-var_stream_map", " ".join(stream_maps),
            playlist_path
        ])
        
        return cmd, video_encoder, variants
    
    def generate_master_playlist(
        self,
        output_dir: Path,
        variants: List[QualityPreset]
    ) -> str:
        """Generate HLS master playlist for ABR variants."""
        lines = ["#EXTM3U", "#EXT-X-VERSION:3"]
        
        for i, variant in enumerate(variants):
            bandwidth = self._get_bandwidth_bps(variant.video_bitrate)
            
            lines.append(
                f"#EXT-X-STREAM-INF:BANDWIDTH={bandwidth},"
                f"RESOLUTION={variant.width}x{variant.height},"
                f"NAME=\"{variant.name}\""
            )
            lines.append(f"stream_{i}.m3u8")
        
        master_content = "\n".join(lines)
        master_path = output_dir / "master.m3u8"
        master_path.write_text(master_content)
        
        return str(master_path)

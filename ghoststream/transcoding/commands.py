"""
FFmpeg command building for various output formats.
Handles HLS, batch, and ABR transcoding commands.
"""

import os
import logging
import httpx
from pathlib import Path
from typing import List, Tuple, Optional

from ..models import OutputConfig, OutputFormat, VideoCodec, Resolution, SubtitleTrack
from ..config import TranscodingConfig, HardwareConfig
from .models import MediaInfo, QualityPreset
from .constants import (
    get_bitrate_map, AUDIO_BITRATE_MAP, QUALITY_LADDER,
    BUFSIZE_MULTIPLIER_HW, BUFSIZE_MULTIPLIER_SW,
    LOOKAHEAD_FRAMES_HW, LOOKAHEAD_FRAMES_SW,
    BFRAMES_HIGH_QUALITY, BFRAMES_STANDARD,
    NVENC_TUNING, GOP_SECONDS,
)
from .filters import FilterBuilder
from .encoders import EncoderSelector
from .hls import HLSPlaylistGenerator, HLSCodecBuilder, HLSConfig

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
    
    def _get_bufsize(self, bitrate: str, is_hw_encoder: bool = True) -> str:
        """
        Calculate bufsize with proper multiplier for encoder type.
        
        Hardware encoders benefit from larger buffers (2x) for quality.
        Software encoders use 1.5x to balance quality and memory.
        """
        value, unit = self._parse_bitrate(bitrate)
        multiplier = BUFSIZE_MULTIPLIER_HW if is_hw_encoder else BUFSIZE_MULTIPLIER_SW
        return f"{int(value * multiplier)}{unit}"
    
    def _get_nvenc_quality_args(self, preset: str, variant_height: int) -> List[str]:
        """
        Get NVENC quality tuning arguments to reduce artifacts.
        
        Uses adaptive quantization, lookahead, and proper B-frame settings.
        """
        tuning = NVENC_TUNING.get(preset, NVENC_TUNING["p4"])
        
        args = [
            "-preset", tuning["preset"],
            "-tune", tuning["tune"],
            "-rc", tuning["rc"],
        ]
        
        # Multipass for better quality (skip for low-latency preset)
        if tuning["multipass"] != "disabled":
            args.extend(["-multipass", tuning["multipass"]])
        
        # Adaptive quantization for better quality in complex scenes
        if tuning["spatial_aq"]:
            args.extend(["-spatial-aq", "1", "-aq-strength", str(tuning["aq_strength"])])
        if tuning["temporal_aq"]:
            args.extend(["-temporal-aq", "1"])
        
        # Lookahead for better bitrate distribution
        args.extend(["-rc-lookahead", str(LOOKAHEAD_FRAMES_HW)])
        
        # B-frames for better compression
        bframes = BFRAMES_HIGH_QUALITY if variant_height >= 1080 else BFRAMES_STANDARD
        args.extend(["-bf", str(bframes)])
        
        # B-frame reference mode
        if tuning["b_ref_mode"] != "disabled":
            args.extend(["-b_ref_mode", tuning["b_ref_mode"]])
        
        return args
    
    def _get_x264_quality_args(self, variant_height: int) -> List[str]:
        """
        Get x264 quality tuning arguments to reduce artifacts.
        
        Uses proper AQ mode, psy-rd, and lookahead.
        """
        args = [
            "-preset", "medium",
            "-profile:v", "high",
            "-tune", "film",
        ]
        
        # Lookahead for better rate control
        args.extend(["-rc-lookahead", str(LOOKAHEAD_FRAMES_SW)])
        
        # B-frames
        bframes = BFRAMES_HIGH_QUALITY if variant_height >= 1080 else BFRAMES_STANDARD
        args.extend(["-bf", str(bframes)])
        
        # AQ mode 2 (variance) for better quality distribution
        args.extend(["-aq-mode", "2"])
        
        return args
    
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
        """Get protocol options for HTTP sources (optimized for Pi/slow networks)."""
        if source.startswith('http://') or source.startswith('https://'):
            return [
                "-headers", "User-Agent: GhostStream/1.0\r\n",
                "-reconnect", "1",
                "-reconnect_streamed", "1",
                "-reconnect_delay_max", "10",
                "-timeout", "60000000",  # 60 second timeout
                "-analyzeduration", "10M",  # Faster analysis
                "-probesize", "10M",
                "-fflags", "+genpts+discardcorrupt",
            ]
        return []
    
    def _download_subtitles(self, subtitles: Optional[List[SubtitleTrack]], output_dir: Path) -> List[Tuple[Path, SubtitleTrack]]:
        """Download subtitle files from URLs into the job temp directory.
        
        Args:
            subtitles: List of SubtitleTrack objects with URLs
            output_dir: Directory to save downloaded subtitle files
            
        Returns:
            List of (local_path, subtitle_track) tuples for successfully downloaded subtitles
        """
        if not subtitles:
            return []
        
        downloaded = []
        
        for i, sub in enumerate(subtitles):
            try:
                # Download subtitle file
                with httpx.Client(timeout=30.0) as client:
                    response = client.get(sub.url)
                    response.raise_for_status()
                    
                    # Save to temp directory with safe filename
                    sub_filename = f"subtitle_{i}_{sub.language}.vtt"
                    sub_path = output_dir / sub_filename
                    sub_path.write_bytes(response.content)
                    
                    downloaded.append((sub_path, sub))
                    logger.info(f"[Subtitles] Downloaded: {sub.label} ({sub.language}) -> {sub_filename}")
            except Exception as e:
                logger.error(f"[Subtitles] Failed to download {sub.url}: {e}")
                continue
        
        logger.info(f"[Subtitles] Downloaded {len(downloaded)} of {len(subtitles)} subtitle tracks")
        return downloaded
    
    def build_hls_command(
        self,
        source: str,
        output_dir: Path,
        output_config: OutputConfig,
        start_time: float = 0,
        media_info: Optional[MediaInfo] = None,
        subtitles: Optional[List[SubtitleTrack]] = None
    ) -> Tuple[List[str], str]:
        """Build FFmpeg command for HLS output with subtitle muxing support."""
        
        video_encoder, video_args = self.encoder_selector.get_video_encoder(
            output_config.video_codec,
            output_config.hw_accel
        )
        audio_encoder, audio_args = self.encoder_selector.get_audio_encoder(
            output_config.audio_codec
        )
        
        # Download subtitle files if provided
        subtitle_files = self._download_subtitles(subtitles, output_dir)
        
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
        
        # Input - video source
        cmd.extend(["-i", source])
        
        # Add subtitle inputs
        for sub_path, _ in subtitle_files:
            cmd.extend(["-i", str(sub_path)])
        
        # Map video and audio streams
        cmd.extend(["-map", "0:v:0", "-map", "0:a:0?"])
        
        # Map subtitle streams (input indices start at 1 for subtitles)
        for i in range(len(subtitle_files)):
            cmd.extend(["-map", f"{i+1}:0"])
        
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
            cmd.extend([
                "-g", str(gop_size),
                "-keyint_min", str(gop_size),
                "-sc_threshold", "0",
                "-flags", "+cgop",
            ])
        
        # Audio encoding with proper channel handling
        cmd.extend(["-c:a", audio_encoder])
        if audio_encoder != "copy":
            channels = media_info.audio_channels if media_info else 2
            audio_br = AUDIO_BITRATE_MAP.get(channels, "128k")
            cmd.extend(["-b:a", audio_br, "-ac", str(min(channels, 2))])
        
        # Subtitle encoding - WebVTT for HLS
        if subtitle_files:
            for i in range(len(subtitle_files)):
                cmd.extend([f"-c:s:{i}", "webvtt"])
        
        # HLS specific options
        segment_duration = self.transcoding_config.segment_duration
        
        # Use var_stream_map for subtitle support (enables master playlist with EXT-X-MEDIA)
        if subtitle_files:
            # With subtitles, use var_stream_map for proper HLS structure
            segment_path = str(output_dir / "stream_%v_%05d.ts").replace("\\", "/")
            playlist_path = str(output_dir / "stream_%v.m3u8").replace("\\", "/")
            
            # Build var_stream_map: v:0,a:0 for video/audio, then s:0,s:1... for subtitles
            stream_map_parts = ["v:0,a:0"]
            for i in range(len(subtitle_files)):
                stream_map_parts.append(f"s:{i},sgroup:subs")
            
            cmd.extend([
                "-f", "hls",
                "-hls_time", str(segment_duration),
                "-hls_list_size", "0",
                "-hls_segment_filename", segment_path,
                "-hls_flags", "independent_segments+append_list",
                "-hls_segment_type", "mpegts",
                "-hls_playlist_type", "event",
                "-master_pl_name", "master.m3u8",
                "-var_stream_map", " ".join(stream_map_parts),
                playlist_path
            ])
        else:
            # No subtitles - use simple single-stream HLS
            playlist_path = output_dir / "master.m3u8"
            segment_pattern = output_dir / "segment_%05d.ts"
            
            cmd.extend([
                "-f", "hls",
                "-hls_time", str(segment_duration),
                "-hls_list_size", "0",
                "-hls_segment_filename", str(segment_pattern),
                "-hls_flags", "independent_segments+append_list",
                "-hls_segment_type", "mpegts",
                "-hls_playlist_type", "event",
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
        """
        Get appropriate ABR variants based on source resolution.
        Ensures a good spread of qualities including low-bandwidth options.
        """
        variants = []
        source_height = media_info.height
        
        # Filter presets that fit within source resolution
        possible_variants = []
        for preset in QUALITY_LADDER:
            if preset.height <= source_height:
                possible_variants.append(preset)
        
        if not possible_variants:
            if QUALITY_LADDER:
                # Source is smaller than smallest preset, just use the smallest
                possible_variants.append(QUALITY_LADDER[-1])
            return possible_variants
            
        # Select up to 4 variants with good spread
        # Always include the highest possible (native-ish)
        # Always include the lowest possible (fallback)
        # Fill in between
        
        if len(possible_variants) <= 4:
            return possible_variants
            
        # We have more than 4, pick 4 strategically
        selected = []
        
        # 1. Highest quality
        selected.append(possible_variants[0])
        
        # 2. Lowest quality (last one)
        selected.append(possible_variants[-1])
        
        # 3. Middle high
        mid_high_idx = len(possible_variants) // 3
        if possible_variants[mid_high_idx] not in selected:
            selected.append(possible_variants[mid_high_idx])
            
        # 4. Middle low
        mid_low_idx = (len(possible_variants) * 2) // 3
        if len(selected) < 4 and possible_variants[mid_low_idx] not in selected:
            selected.append(possible_variants[mid_low_idx])
            
        # Sort by resolution/bitrate descending (restore order)
        selected.sort(key=lambda x: (x.height, self._parse_bitrate(x.video_bitrate)[0]), reverse=True)
        
        return selected
    
    def build_abr_command(
        self,
        source: str,
        output_dir: Path,
        output_config: OutputConfig,
        media_info: MediaInfo,
        start_time: float = 0,
        variants: Optional[List[QualityPreset]] = None,
        subtitles: Optional[List[SubtitleTrack]] = None
    ) -> Tuple[List[str], str, List[QualityPreset]]:
        """Build FFmpeg command for ABR HLS with multiple quality variants and subtitle support."""
        
        video_encoder, video_args = self.encoder_selector.get_video_encoder(
            output_config.video_codec,
            output_config.hw_accel
        )
        audio_encoder, _ = self.encoder_selector.get_audio_encoder(output_config.audio_codec)
        
        if variants is None:
            variants = self.get_abr_variants(media_info)
        
        # Download subtitle files if provided
        subtitle_files = self._download_subtitles(subtitles, output_dir)
        
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
        
        # Input - video source
        cmd.extend(["-i", source])
        
        # Add subtitle inputs
        for sub_path, _ in subtitle_files:
            cmd.extend(["-i", str(sub_path)])
        
        # Build filter complex for multiple outputs
        filter_parts = self.filter_builder.build_abr_filter_complex(
            variants, media_info, needs_cpu_filters, video_encoder
        )
        
        map_args = []
        stream_maps = []
        
        # Determine if using hardware encoder
        is_hw = "nvenc" in video_encoder or "qsv" in video_encoder or "vaapi" in video_encoder
        
        # Track audio stream index for var_stream_map
        audio_stream_idx = 0
        
        for i, variant in enumerate(variants):
            # Map video output from filter_complex
            map_args.extend(["-map", f"[v{i}]"])
            
            # Map audio for this variant (each variant gets its own audio mapping)
            # This avoids "Same elementary stream found more than once" error
            map_args.extend(["-map", "0:a:0?"])
            
            # Video encoding for this variant
            map_args.extend([f"-c:v:{i}", video_encoder])
            map_args.extend([f"-b:v:{i}", variant.video_bitrate])
            
            # Netflix-level rate control: maxrate slightly above target for headroom
            value, unit = self._parse_bitrate(variant.video_bitrate)
            maxrate = f"{value * 1.1:.1f}{unit}"  # 10% headroom
            map_args.extend([f"-maxrate:v:{i}", maxrate])
            map_args.extend([f"-bufsize:v:{i}", self._get_bufsize(variant.video_bitrate, is_hw)])
            
            # Quality tuning based on encoder type
            if "nvenc" in video_encoder:
                # NVENC quality optimization
                nvenc_args = self._get_nvenc_quality_args(variant.hw_preset, variant.height)
                for j in range(0, len(nvenc_args), 2):
                    if j + 1 < len(nvenc_args):
                        map_args.extend([f"{nvenc_args[j]}:v:{i}", nvenc_args[j + 1]])
            elif "libx264" in video_encoder:
                # x264 quality optimization
                x264_args = self._get_x264_quality_args(variant.height)
                for j in range(0, len(x264_args), 2):
                    if j + 1 < len(x264_args):
                        map_args.extend([f"{x264_args[j]}:v:{i}", x264_args[j + 1]])
            elif "libx265" in video_encoder:
                map_args.extend([f"-preset:v:{i}", "medium"])
                map_args.extend([f"-x265-params:v:{i}", "aq-mode=2:rc-lookahead=20"])
            
            # GOP/Keyframe alignment for proper ABR switching
            fps = media_info.fps if media_info.fps > 0 else 30
            gop = int(fps * GOP_SECONDS)  # Use configured GOP seconds
            map_args.extend([
                f"-g:v:{i}", str(gop),
                f"-keyint_min:v:{i}", str(gop),
                f"-sc_threshold:v:{i}", "0",  # Disable scene detection for consistent GOPs
                f"-flags:v:{i}", "+cgop",     # Closed GOP for better seeking
            ])
            
            # Audio encoding for this variant
            map_args.extend([f"-c:a:{i}", audio_encoder])
            if audio_encoder != "copy":
                map_args.extend([f"-b:a:{i}", "128k", f"-ac:{i}", "2"])
            
            # Build var_stream_map entry: v:i,a:i (video stream i, audio stream i)
            stream_maps.append(f"v:{i},a:{i}")
        
        # Map subtitle streams if present
        if subtitle_files:
            for sub_idx in range(len(subtitle_files)):
                # Subtitle input indices start after video source (input 0)
                input_idx = sub_idx + 1
                map_args.extend(["-map", f"{input_idx}:0"])
                # Subtitle codec
                map_args.extend([f"-c:s:{sub_idx}", "webvtt"])
                # Add subtitle streams to var_stream_map with sgroup
                stream_maps.append(f"s:{sub_idx},sgroup:subs")
        
        # Apply filter complex
        if filter_parts:
            cmd.extend(["-filter_complex", ";".join(filter_parts)])
        
        cmd.extend(map_args)
        
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
            "-hls_playlist_type", "event",
            "-master_pl_name", "master.m3u8",
            "-hls_segment_filename", segment_path,
            "-var_stream_map", " ".join(stream_maps),
            playlist_path
        ])
        
        return cmd, video_encoder, variants
    
    def generate_master_playlist(
        self,
        output_dir: Path,
        variants: List[QualityPreset],
        media_info: Optional[MediaInfo] = None,
        video_codec: str = "h264"
    ) -> str:
        """
        Generate Netflix-quality HLS master playlist.
        
        Includes proper CODECS, BANDWIDTH, AVERAGE-BANDWIDTH,
        and RESOLUTION attributes for optimal player compatibility.
        """
        # Use the HLS module for Netflix-level playlist generation
        hls_gen = HLSPlaylistGenerator(HLSConfig(
            segment_duration=self.transcoding_config.segment_duration
        ))
        
        if media_info:
            hls_variants = hls_gen.build_variants(variants, media_info, video_codec)
            return hls_gen.generate_master_playlist(output_dir, hls_variants)
        
        # Fallback to basic playlist if no media info
        lines = [
            "#EXTM3U",
            "#EXT-X-VERSION:6",
            "#EXT-X-INDEPENDENT-SEGMENTS",
        ]
        
        # Sort by bandwidth descending
        sorted_variants = sorted(
            enumerate(variants),
            key=lambda x: self._get_bandwidth_bps(x[1].video_bitrate),
            reverse=True
        )
        
        for orig_idx, variant in sorted_variants:
            bandwidth = self._get_bandwidth_bps(variant.video_bitrate)
            avg_bandwidth = int(bandwidth / 1.4)  # Average is ~70% of peak
            
            # Get proper codec string
            codecs = HLSCodecBuilder.get_full_codec_string(
                video_codec, variant.width, variant.height
            )
            
            lines.append(
                f"#EXT-X-STREAM-INF:BANDWIDTH={bandwidth},"
                f"AVERAGE-BANDWIDTH={avg_bandwidth},"
                f"RESOLUTION={variant.width}x{variant.height},"
                f'CODECS="{codecs}",'
                f"NAME=\"{variant.name}\""
            )
            lines.append(f"stream_{orig_idx}.m3u8")
        
        master_content = "\n".join(lines)
        master_path = output_dir / "master.m3u8"
        master_path.write_text(master_content)
        
        return str(master_path)

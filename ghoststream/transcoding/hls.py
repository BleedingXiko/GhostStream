"""
Netflix-level HLS streaming optimizations.

Implements industry best practices for adaptive bitrate streaming:
- Proper CODECS attribute for player compatibility
- AVERAGE-BANDWIDTH for smoother quality switching
- I-frame playlists for trick play (fast forward/rewind)
- Optimal segment duration and GOP alignment
- Buffer management recommendations
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Dict, Tuple

from .models import QualityPreset, MediaInfo

logger = logging.getLogger(__name__)


# Netflix-recommended segment durations based on content type
SEGMENT_DURATION_LIVE = 2  # Low latency for live
SEGMENT_DURATION_VOD = 4   # Standard VOD (good balance)
SEGMENT_DURATION_FILM = 6  # Long-form content (fewer requests)


@dataclass
class HLSVariant:
    """Represents an HLS variant stream with all required attributes."""
    preset: QualityPreset
    bandwidth: int           # Peak bandwidth in bits/s
    average_bandwidth: int   # Average bandwidth in bits/s
    codecs: str             # RFC 6381 codec string
    frame_rate: float = 0.0
    audio_group: str = "audio"
    
    @property
    def stream_inf(self) -> str:
        """Generate EXT-X-STREAM-INF line."""
        parts = [
            f"BANDWIDTH={self.bandwidth}",
            f"AVERAGE-BANDWIDTH={self.average_bandwidth}",
            f"RESOLUTION={self.preset.width}x{self.preset.height}",
            f'CODECS="{self.codecs}"',
        ]
        if self.frame_rate > 0:
            parts.append(f"FRAME-RATE={self.frame_rate:.3f}")
        parts.append(f'AUDIO="{self.audio_group}"')
        
        return ",".join(parts)


@dataclass  
class HLSConfig:
    """Configuration for HLS stream generation."""
    segment_duration: int = 4
    playlist_type: str = "event"  # 'event' or 'vod'
    
    # Netflix-level features
    enable_independent_segments: bool = True
    enable_iframe_playlist: bool = False  # I-frame playlist for trick play
    enable_byte_range: bool = False       # Byte-range segments (reduces files)
    
    # Buffer recommendations
    min_buffer_time: float = 30.0   # Minimum buffer before playback
    max_buffer_time: float = 60.0   # Maximum buffer to maintain
    rebuffer_target: float = 3.0    # Target buffer after rebuffer
    
    # Quality switching
    bandwidth_safety_factor: float = 0.8  # Use 80% of estimated bandwidth
    switch_down_threshold: float = 0.7    # Switch down if buffer < 70% target
    switch_up_threshold: float = 1.5      # Switch up if buffer > 150% target
    
    # GOP and keyframe alignment
    gop_seconds: float = 2.0        # GOP length in seconds (2s = Netflix standard)
    force_keyframe_alignment: bool = True


class HLSCodecBuilder:
    """Builds RFC 6381 compliant codec strings for HLS."""
    
    # H.264 profile/level mappings
    H264_PROFILES = {
        "baseline": "42",
        "main": "4D",
        "high": "64",
    }
    
    H264_LEVELS = {
        "3.0": "1E",
        "3.1": "1F",
        "4.0": "28",
        "4.1": "29",
        "4.2": "2A",
        "5.0": "32",
        "5.1": "33",
        "5.2": "34",
        "6.0": "3C",
        "6.1": "3D",
        "6.2": "3E",
    }
    
    # H.265/HEVC profile mappings
    HEVC_PROFILES = {
        "main": "1",
        "main10": "2",
    }
    
    @classmethod
    def get_h264_codec(
        cls,
        width: int,
        height: int,
        profile: str = "high",
        fps: float = 30.0
    ) -> str:
        """
        Generate H.264 codec string with appropriate level.
        
        Format: avc1.PPCCLL (profile, constraint, level)
        """
        profile_hex = cls.H264_PROFILES.get(profile, "64")  # Default to high
        
        # Determine level based on resolution and framerate
        level = cls._get_h264_level(width, height, fps)
        level_hex = cls.H264_LEVELS.get(level, "28")  # Default to 4.0
        
        # Constraint flags (00 for unconstrained)
        return f"avc1.{profile_hex}00{level_hex}"
    
    @classmethod
    def _get_h264_level(cls, width: int, height: int, fps: float) -> str:
        """Determine H.264 level based on resolution and framerate."""
        pixels = width * height
        pixels_per_sec = pixels * fps
        
        # Level selection based on macroblocks/second
        if pixels_per_sec <= 983040:      # 720p30 or lower
            return "3.1"
        elif pixels_per_sec <= 2073600:   # 1080p30
            return "4.0"
        elif pixels_per_sec <= 4177920:   # 1080p60
            return "4.2"
        elif pixels_per_sec <= 8355840:   # 4K30
            return "5.1"
        elif pixels_per_sec <= 16711680:  # 4K60
            return "5.2"
        else:
            return "6.0"
    
    @classmethod
    def get_hevc_codec(
        cls,
        width: int,
        height: int,
        profile: str = "main",
        bit_depth: int = 8
    ) -> str:
        """
        Generate HEVC codec string.
        
        Format: hvc1.P.L.TL (profile, tier, level)
        """
        if bit_depth == 10:
            profile = "main10"
        
        profile_num = cls.HEVC_PROFILES.get(profile, "1")
        tier = "L"  # Main tier
        
        # Determine level
        pixels = width * height
        if pixels <= 921600:      # 720p
            level = "93"  # 3.1
        elif pixels <= 2073600:   # 1080p
            level = "120"  # 4.0
        elif pixels <= 8294400:   # 4K
            level = "150"  # 5.0
        else:
            level = "153"  # 5.1
        
        return f"hvc1.{profile_num}.4.{tier}{level}"
    
    @classmethod
    def get_aac_codec(cls, channels: int = 2) -> str:
        """Generate AAC codec string."""
        if channels <= 2:
            return "mp4a.40.2"  # AAC-LC
        else:
            return "mp4a.40.5"  # HE-AAC for multichannel
    
    @classmethod
    def get_full_codec_string(
        cls,
        video_codec: str,
        width: int,
        height: int,
        fps: float = 30.0,
        audio_channels: int = 2
    ) -> str:
        """Get full codec string for video + audio."""
        if "h265" in video_codec.lower() or "hevc" in video_codec.lower():
            video = cls.get_hevc_codec(width, height)
        else:
            video = cls.get_h264_codec(width, height, fps=fps)
        
        audio = cls.get_aac_codec(audio_channels)
        return f"{video},{audio}"


class HLSPlaylistGenerator:
    """Generates Netflix-quality HLS playlists."""
    
    def __init__(self, config: Optional[HLSConfig] = None):
        self.config = config or HLSConfig()
        self.codec_builder = HLSCodecBuilder()
    
    def calculate_bandwidth(self, bitrate_str: str) -> Tuple[int, int]:
        """
        Calculate peak and average bandwidth from bitrate string.
        
        Returns (peak_bandwidth, average_bandwidth) in bits/second.
        Netflix recommends peak = 1.4x average for ABR stability.
        """
        # Parse bitrate
        bitrate_str = bitrate_str.strip().upper()
        if bitrate_str.endswith('M'):
            avg_bps = int(float(bitrate_str[:-1]) * 1_000_000)
        elif bitrate_str.endswith('K'):
            avg_bps = int(float(bitrate_str[:-1]) * 1_000)
        else:
            avg_bps = int(float(bitrate_str))
        
        # Add audio bitrate (assume 128kbps stereo)
        avg_bps += 128_000
        
        # Peak is typically 1.4x average (Netflix recommendation)
        peak_bps = int(avg_bps * 1.4)
        
        return peak_bps, avg_bps
    
    def build_variants(
        self,
        presets: List[QualityPreset],
        media_info: MediaInfo,
        video_codec: str = "h264"
    ) -> List[HLSVariant]:
        """Build HLS variants from quality presets."""
        variants = []
        fps = media_info.fps if media_info.fps > 0 else 30.0
        audio_channels = media_info.audio_channels if hasattr(media_info, 'audio_channels') else 2
        
        for preset in presets:
            peak_bw, avg_bw = self.calculate_bandwidth(preset.video_bitrate)
            codecs = self.codec_builder.get_full_codec_string(
                video_codec, preset.width, preset.height, fps, audio_channels
            )
            
            variants.append(HLSVariant(
                preset=preset,
                bandwidth=peak_bw,
                average_bandwidth=avg_bw,
                codecs=codecs,
                frame_rate=fps
            ))
        
        return variants
    
    def generate_master_playlist(
        self,
        output_dir: Path,
        variants: List[HLSVariant],
        include_audio_group: bool = True,
        subtitle_tracks: Optional[List[Dict]] = None
    ) -> str:
        """
        Generate Netflix-quality master playlist with subtitle support.
        
        Features:
        - EXT-X-VERSION: 6 for modern features
        - EXT-X-INDEPENDENT-SEGMENTS for better caching
        - Full CODECS, BANDWIDTH, AVERAGE-BANDWIDTH
        - EXT-X-MEDIA subtitle entries for WebVTT subtitles
        - Sorted by bandwidth (highest first for faster quality selection)
        
        Args:
            subtitle_tracks: List of dicts with 'label', 'language', 'default' keys
        """
        lines = [
            "#EXTM3U",
            "#EXT-X-VERSION:6",
        ]
        
        # Independent segments for better CDN caching
        if self.config.enable_independent_segments:
            lines.append("#EXT-X-INDEPENDENT-SEGMENTS")
        
        # Subtitle group definitions (added before audio/video for proper HLS.js support)
        if subtitle_tracks:
            for i, sub in enumerate(subtitle_tracks):
                label = sub.get('label', f'Subtitle {i+1}')
                language = sub.get('language', 'und')
                is_default = 'YES' if sub.get('default', i == 0) else 'NO'
                
                # Format: EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="...",LANGUAGE="...",URI="..."
                lines.append(
                    f'#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="{label}",'
                    f'DEFAULT={is_default},AUTOSELECT={is_default},FORCED=NO,'
                    f'LANGUAGE="{language}",URI="stream_{len(variants) + i}.m3u8"'
                )
        
        # Audio group definition (for separate audio tracks)
        if include_audio_group:
            lines.append(
                f'#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",NAME="English",'
                f'DEFAULT=YES,AUTOSELECT=YES,LANGUAGE="en",URI="stream_0.m3u8"'
            )
        
        # Sort variants by bandwidth (highest first)
        sorted_variants = sorted(variants, key=lambda v: v.bandwidth, reverse=True)
        
        # Add stream info for each variant
        for i, variant in enumerate(sorted_variants):
            lines.append(f"#EXT-X-STREAM-INF:{variant.stream_inf}")
            lines.append(f"stream_{i}.m3u8")
        
        # I-frame playlist references (for trick play)
        if self.config.enable_iframe_playlist:
            for i, variant in enumerate(sorted_variants):
                lines.append(
                    f"#EXT-X-I-FRAME-STREAM-INF:BANDWIDTH={variant.bandwidth // 10},"
                    f"RESOLUTION={variant.preset.width}x{variant.preset.height},"
                    f'CODECS="{variant.codecs.split(",")[0]}",'
                    f'URI="stream_{i}_iframe.m3u8"'
                )
        
        content = "\n".join(lines)
        
        # Write to file
        master_path = output_dir / "master.m3u8"
        master_path.write_text(content)
        
        logger.info(f"[HLS] Generated master playlist with {len(variants)} variants")
        return str(master_path)
    
    def get_ffmpeg_hls_args(
        self,
        output_dir: Path,
        num_variants: int = 1
    ) -> List[str]:
        """Get FFmpeg arguments for Netflix-quality HLS output."""
        cfg = self.config
        
        # Use forward slashes for FFmpeg
        segment_path = str(output_dir / "stream_%v_%05d.ts").replace("\\", "/")
        playlist_path = str(output_dir / "stream_%v.m3u8").replace("\\", "/")
        
        args = [
            "-f", "hls",
            "-hls_time", str(cfg.segment_duration),
            "-hls_list_size", "0",  # Keep all segments in playlist
            "-hls_playlist_type", cfg.playlist_type,
            "-hls_segment_type", "mpegts",
            "-master_pl_name", "master.m3u8",
            "-hls_segment_filename", segment_path,
        ]
        
        # HLS flags for Netflix-level quality
        hls_flags = ["independent_segments", "append_list"]
        if cfg.enable_byte_range:
            hls_flags.append("single_file")
        
        args.extend(["-hls_flags", "+".join(hls_flags)])
        
        # Variant stream map for ABR
        if num_variants > 1:
            stream_maps = [f"v:{i},a:0" for i in range(num_variants)]
            args.extend(["-var_stream_map", " ".join(stream_maps)])
        
        args.append(playlist_path)
        
        return args
    
    def get_gop_args(self, fps: float) -> List[str]:
        """Get GOP/keyframe arguments for proper segment alignment."""
        cfg = self.config
        gop_frames = int(fps * cfg.gop_seconds)
        
        return [
            "-g", str(gop_frames),           # GOP size
            "-keyint_min", str(gop_frames),  # Minimum keyframe interval
            "-sc_threshold", "0",            # Disable scene change detection
            "-flags", "+cgop",               # Closed GOP
        ]


@dataclass
class StreamingRecommendations:
    """Netflix-style streaming recommendations based on content analysis."""
    segment_duration: int
    gop_seconds: float
    min_variants: int
    max_variants: int
    buffer_size_ratio: float  # bufsize = bitrate * ratio
    lookahead_frames: int
    
    @classmethod
    def for_content(cls, media_info: MediaInfo, is_live: bool = False) -> "StreamingRecommendations":
        """Generate recommendations based on content type."""
        fps = media_info.fps if media_info.fps > 0 else 30.0
        
        if is_live:
            return cls(
                segment_duration=2,
                gop_seconds=1.0,
                min_variants=2,
                max_variants=3,
                buffer_size_ratio=1.0,
                lookahead_frames=int(fps * 0.5)
            )
        
        # Standard VOD recommendations
        duration = media_info.duration
        
        if duration > 3600:  # Long-form (>1 hour)
            return cls(
                segment_duration=6,
                gop_seconds=2.0,
                min_variants=3,
                max_variants=5,
                buffer_size_ratio=2.0,
                lookahead_frames=int(fps * 2)
            )
        elif duration > 600:  # Medium (>10 min)
            return cls(
                segment_duration=4,
                gop_seconds=2.0,
                min_variants=3,
                max_variants=4,
                buffer_size_ratio=1.5,
                lookahead_frames=int(fps * 1.5)
            )
        else:  # Short-form
            return cls(
                segment_duration=2,
                gop_seconds=1.0,
                min_variants=2,
                max_variants=3,
                buffer_size_ratio=1.5,
                lookahead_frames=int(fps * 1)
            )


# Global HLS playlist generator
_hls_generator: Optional[HLSPlaylistGenerator] = None


def get_hls_generator(config: Optional[HLSConfig] = None) -> HLSPlaylistGenerator:
    """Get or create HLS playlist generator."""
    global _hls_generator
    if _hls_generator is None or config is not None:
        _hls_generator = HLSPlaylistGenerator(config)
    return _hls_generator

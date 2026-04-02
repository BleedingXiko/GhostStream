"""
Data models for transcoding operations.
"""

from dataclasses import dataclass


@dataclass
class QualityPreset:
    """Professional quality preset for transcoding."""
    name: str
    width: int
    height: int
    video_bitrate: str
    audio_bitrate: str
    crf: int  # For software encoding
    hw_preset: str  # For hardware encoding


@dataclass
class TranscodeProgress:
    """Progress information for a transcode job."""
    frame: int = 0
    fps: float = 0.0
    bitrate: str = "0kbits/s"
    total_size: int = 0
    time: float = 0.0
    speed: float = 0.0
    percent: float = 0.0
    stage: str = "transcoding"  # transcoding, remuxing, finalizing


@dataclass
class MediaInfo:
    """Information about a media file."""
    duration: float = 0.0
    width: int = 0
    height: int = 0
    video_codec: str = ""
    audio_codec: str = ""
    bitrate: int = 0
    fps: float = 0.0
    # Extended info for smart transcoding
    pixel_format: str = ""
    color_transfer: str = ""
    color_primaries: str = ""
    audio_channels: int = 2
    audio_sample_rate: int = 48000
    is_hdr: bool = False
    is_10bit: bool = False
    has_bframes: bool = True

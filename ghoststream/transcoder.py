"""
FFmpeg transcoding engine for GhostStream
Professional-grade transcoding with bulletproof error handling

This module re-exports from the modular transcoding package for backwards compatibility.
For new code, import directly from ghoststream.transcoding.
"""

# Re-export everything from the modular transcoding package
from .transcoding import (
    # Models
    QualityPreset,
    TranscodeProgress,
    MediaInfo,
    # Constants
    QUALITY_LADDER,
    TONEMAP_FILTER,
    TONEMAP_FILTER_SIMPLE,
    AUDIO_BITRATE_MAP,
    MAX_RETRIES,
    RETRY_DELAY,
    get_resolution_map,
    get_bitrate_map,
    # Classes
    FilterBuilder,
    EncoderSelector,
    MediaProbe,
    CommandBuilder,
    TranscodeEngine,
)

__all__ = [
    "QualityPreset",
    "TranscodeProgress",
    "MediaInfo",
    "QUALITY_LADDER",
    "TONEMAP_FILTER",
    "TONEMAP_FILTER_SIMPLE",
    "AUDIO_BITRATE_MAP",
    "MAX_RETRIES",
    "RETRY_DELAY",
    "get_resolution_map",
    "get_bitrate_map",
    "FilterBuilder",
    "EncoderSelector",
    "MediaProbe",
    "CommandBuilder",
    "TranscodeEngine",
]

"""
Modular transcoding package for GhostStream.
Professional-grade FFmpeg transcoding with HDR support and hardware acceleration.
"""

from .models import QualityPreset, TranscodeProgress, MediaInfo
from .constants import (
    QUALITY_LADDER,
    TONEMAP_FILTER,
    TONEMAP_FILTER_SIMPLE,
    AUDIO_BITRATE_MAP,
    MAX_RETRIES,
    RETRY_DELAY,
    get_resolution_map,
    get_bitrate_map,
)
from .filters import FilterBuilder
from .encoders import EncoderSelector
from .probe import MediaProbe
from .commands import CommandBuilder
from .engine import TranscodeEngine
from .adaptive import (
    HardwareTier,
    PowerSource,
    CPUInfo,
    SystemProfile,
    SystemMetrics,
    SystemMonitor,
    TranscodeJob,
    LoadBalancer,
    HardwareProfiler,
    AdaptiveQualitySelector,
    AdaptiveTranscodeManager,
    get_hardware_profiler,
    get_adaptive_quality_selector,
    get_adaptive_manager,
)

__all__ = [
    # Models
    "QualityPreset",
    "TranscodeProgress",
    "MediaInfo",
    # Constants
    "QUALITY_LADDER",
    "TONEMAP_FILTER",
    "TONEMAP_FILTER_SIMPLE",
    "AUDIO_BITRATE_MAP",
    "MAX_RETRIES",
    "RETRY_DELAY",
    "get_resolution_map",
    "get_bitrate_map",
    # Classes
    "FilterBuilder",
    "EncoderSelector",
    "MediaProbe",
    "CommandBuilder",
    "TranscodeEngine",
    # Adaptive
    "HardwareTier",
    "PowerSource",
    "CPUInfo",
    "SystemProfile",
    "SystemMetrics",
    "SystemMonitor",
    "TranscodeJob",
    "LoadBalancer",
    "HardwareProfiler",
    "AdaptiveQualitySelector",
    "AdaptiveTranscodeManager",
    "get_hardware_profiler",
    "get_adaptive_quality_selector",
    "get_adaptive_manager",
]

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
    MIN_STALL_TIMEOUT,
    STALL_TIMEOUT_PER_SEGMENT,
    get_resolution_map,
    get_bitrate_map,
)
from .filters import FilterBuilder
from .encoders import EncoderSelector
from .probe import MediaProbe
from .commands import CommandBuilder
from .engine import TranscodeEngine
from .error_classifier import (
    FFmpegError,
    ErrorClassifier,
    get_error_classifier,
    FFMPEG_ERROR_MAP,
)
from .job_context import (
    JobContext,
    JobRegistry,
    JobRegistryEntry,
)
from .ffmpeg_runner import (
    FFmpegRunner,
    ProgressParser,
    StallConfig,
)
from .hls import (
    HLSVariant,
    HLSConfig,
    HLSCodecBuilder,
    HLSPlaylistGenerator,
    StreamingRecommendations,
    get_hls_generator,
)
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
from .worker import (
    WorkerState,
    WorkerStats,
    FFmpegWorker,
    FFmpegWorkerPool,
    get_worker_pool,
    init_worker_pool,
    shutdown_worker_pool,
)
from .scheduler import (
    JobPriority,
    JobState,
    ScheduledJob,
    JobScheduler,
    get_scheduler,
    init_scheduler,
    shutdown_scheduler,
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
    "MIN_STALL_TIMEOUT",
    "STALL_TIMEOUT_PER_SEGMENT",
    "get_resolution_map",
    "get_bitrate_map",
    # Classes
    "FilterBuilder",
    "EncoderSelector",
    "MediaProbe",
    "CommandBuilder",
    "TranscodeEngine",
    # Error Classification
    "FFmpegError",
    "ErrorClassifier",
    "get_error_classifier",
    "FFMPEG_ERROR_MAP",
    # Job Management
    "JobContext",
    "JobRegistry",
    "JobRegistryEntry",
    # FFmpeg Runner
    "FFmpegRunner",
    "ProgressParser",
    "StallConfig",
    # HLS (Netflix-level)
    "HLSVariant",
    "HLSConfig",
    "HLSCodecBuilder",
    "HLSPlaylistGenerator",
    "StreamingRecommendations",
    "get_hls_generator",
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
    # Worker Pool
    "WorkerState",
    "WorkerStats",
    "FFmpegWorker",
    "FFmpegWorkerPool",
    "get_worker_pool",
    "init_worker_pool",
    "shutdown_worker_pool",
    # Scheduler
    "JobPriority",
    "JobState",
    "ScheduledJob",
    "JobScheduler",
    "get_scheduler",
    "init_scheduler",
    "shutdown_scheduler",
]

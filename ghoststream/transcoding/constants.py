"""
Constants and presets for transcoding operations.
"""

from typing import List, Dict, Tuple, TYPE_CHECKING
from .models import QualityPreset

if TYPE_CHECKING:
    from ..models import Resolution


# =============================================================================
# NETFLIX-LEVEL QUALITY LADDER
# =============================================================================
# Key improvements over basic ladder:
# - Proper bitrate spacing (1.5-2x between levels) for smooth ABR switching
# - Optimized CRF values for quality-rate tradeoff
# - Hardware presets tuned for quality vs speed
# - Audio bitrates matched to video quality tier

QUALITY_LADDER: List[QualityPreset] = [
    # 4K tier - high quality for premium content
    QualityPreset("4K", 3840, 2160, "18M", "256k", 18, "p4"),
    QualityPreset("4K-mid", 3840, 2160, "12M", "192k", 20, "p4"),
    # 1080p tier - primary quality for most users  
    QualityPreset("1080p", 1920, 1080, "6M", "192k", 20, "p4"),
    QualityPreset("1080p-mid", 1920, 1080, "4M", "128k", 22, "p4"),
    # 720p tier - mobile/tablet quality
    QualityPreset("720p", 1280, 720, "2.5M", "128k", 22, "p4"),
    QualityPreset("720p-low", 1280, 720, "1.5M", "96k", 24, "p5"),
    # SD tier - low bandwidth fallback
    QualityPreset("480p", 854, 480, "1M", "96k", 24, "p5"),
    QualityPreset("360p", 640, 360, "600k", "64k", 26, "p6"),
    QualityPreset("240p", 426, 240, "300k", "48k", 28, "p6"),
]


# HDR to SDR tone mapping filter (Mobius for natural colors)
# Must specify input colorspace (tin/pin/min) for zscale to find conversion path
TONEMAP_FILTER = (
    "zscale=tin=smpte2084:min=bt2020nc:pin=bt2020:t=linear:npl=100,"
    "format=gbrpf32le,"
    "zscale=p=bt709,"
    "tonemap=tonemap=mobius:desat=0,"
    "zscale=t=bt709:m=bt709:r=tv,"
    "format=yuv420p"
)

# Simpler tonemap for systems without zscale
TONEMAP_FILTER_SIMPLE = (
    "setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709,"
    "format=yuv420p"
)


# Resolution mappings - use string keys to avoid circular import
# These get converted to Resolution enum keys at runtime
RESOLUTION_MAP_RAW: Dict[str, Tuple[int, int]] = {
    "uhd_4k": (3840, 2160),
    "fhd_1080p": (1920, 1080),
    "hd_720p": (1280, 720),
    "sd_480p": (854, 480),
}

# Bitrate recommendations based on resolution - use string keys
BITRATE_MAP_RAW: Dict[str, str] = {
    "uhd_4k": "20M",
    "fhd_1080p": "8M",
    "hd_720p": "4M",
    "sd_480p": "1.5M",
    "original": "8M",
}


def get_resolution_map():
    """Get resolution map with proper Resolution enum keys."""
    from ..models import Resolution
    return {
        Resolution.UHD_4K: (3840, 2160),
        Resolution.FHD_1080P: (1920, 1080),
        Resolution.HD_720P: (1280, 720),
        Resolution.SD_480P: (854, 480),
    }


def get_bitrate_map():
    """Get bitrate map with proper Resolution enum keys."""
    from ..models import Resolution
    return {
        Resolution.UHD_4K: "20M",
        Resolution.FHD_1080P: "8M",
        Resolution.HD_720P: "4M",
        Resolution.SD_480P: "1.5M",
        Resolution.ORIGINAL: "8M",
    }


# For backwards compatibility - these are populated lazily
RESOLUTION_MAP: Dict = {}
BITRATE_MAP: Dict = {}


def _init_maps():
    """Initialize maps with Resolution enum keys. Called on first use."""
    global RESOLUTION_MAP, BITRATE_MAP
    if not RESOLUTION_MAP:
        RESOLUTION_MAP.update(get_resolution_map())
    if not BITRATE_MAP:
        BITRATE_MAP.update(get_bitrate_map())

# Audio bitrate based on channels
AUDIO_BITRATE_MAP = {
    1: "64k",   # Mono
    2: "128k",  # Stereo
    6: "384k",  # 5.1
    8: "512k",  # 7.1
}

# Retry configuration
MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds
MAX_RETRY_DELAY = 60  # Max delay for exponential backoff (seconds)
TRANSIENT_INFINITE_RETRY = True  # Keep retrying transient network errors indefinitely

# Stall timeout configuration
MIN_STALL_TIMEOUT = 120  # Minimum stall timeout in seconds
STALL_TIMEOUT_PER_SEGMENT = 10  # Additional seconds per segment duration

# Stderr buffer configuration
STDERR_BUFFER_SIZE = 200  # Lines to keep in stderr buffer
STDERR_EARLY_BUFFER_SIZE = 50  # Lines to preserve from early stderr (errors)


# =============================================================================
# ENCODING OPTIMIZATION PARAMETERS
# =============================================================================
# These parameters are tuned to reduce artifacts and improve quality

# Buffer size multipliers (bufsize = bitrate * multiplier)
# Higher values = more consistent quality but slightly more latency
BUFSIZE_MULTIPLIER_HW = 2.0   # Hardware encoders (NVENC, QSV, etc.)
BUFSIZE_MULTIPLIER_SW = 1.5   # Software encoders (libx264, libx265)

# Rate control lookahead - helps prevent artifacts during scene changes
# Higher values = better quality but more memory usage
LOOKAHEAD_FRAMES_HW = 32   # NVENC rc-lookahead
LOOKAHEAD_FRAMES_SW = 40   # x264 rc-lookahead

# B-frame configuration - balances compression vs encoding complexity
BFRAMES_HIGH_QUALITY = 4    # For 1080p+ content
BFRAMES_STANDARD = 3        # For 720p content
BFRAMES_LOW_LATENCY = 1     # For low-latency streaming

# NVENC quality tuning
NVENC_TUNING = {
    "p4": {  # Balanced quality/speed (default)
        "preset": "p4",
        "tune": "hq",
        "rc": "vbr",
        "multipass": "qres",
        "spatial_aq": 1,
        "temporal_aq": 1,
        "aq_strength": 8,
        "b_ref_mode": "middle",
    },
    "p5": {  # Quality priority
        "preset": "p5",
        "tune": "hq",
        "rc": "vbr",
        "multipass": "qres",
        "spatial_aq": 1,
        "temporal_aq": 1,
        "aq_strength": 10,
        "b_ref_mode": "middle",
    },
    "p6": {  # Speed priority (low res)
        "preset": "p6",
        "tune": "ll",  # Low latency
        "rc": "vbr",
        "multipass": "disabled",
        "spatial_aq": 1,
        "temporal_aq": 0,
        "aq_strength": 8,
        "b_ref_mode": "disabled",
    },
}

# x264 quality tuning
X264_TUNING = {
    "high_quality": {
        "preset": "slow",
        "tune": "film",
        "profile": "high",
        "aq_mode": 3,  # Variance AQ with bias to dark scenes
        "psy_rd": "1.0:0.0",
    },
    "balanced": {
        "preset": "medium",
        "tune": "film",
        "profile": "high",
        "aq_mode": 2,  # Variance AQ
        "psy_rd": "1.0:0.0",
    },
    "fast": {
        "preset": "fast",
        "tune": "zerolatency",
        "profile": "main",
        "aq_mode": 1,
        "psy_rd": "0.5:0.0",
    },
}

# GOP (Group of Pictures) configuration
GOP_SECONDS = 2.0          # Standard GOP length in seconds
MIN_KEYINT_RATIO = 1.0     # keyint_min = GOP * this ratio
SCENE_CHANGE_THRESHOLD = 0  # Disable scene change detection for ABR

# HLS segment configuration
HLS_SEGMENT_DURATION = 4      # Segment duration in seconds
HLS_LIST_SIZE = 0             # Keep all segments (0 = unlimited)
HLS_DELETE_THRESHOLD = 1      # Keep extra segments before deleting

# Quality-based bitrate spacing (Netflix recommendation)
# Adjacent quality levels should have 1.5-2.0x bitrate difference
MIN_BITRATE_RATIO = 1.5
MAX_BITRATE_RATIO = 2.5

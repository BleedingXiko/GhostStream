"""
Constants and presets for transcoding operations.
"""

from typing import List, Dict, Tuple, TYPE_CHECKING
from .models import QualityPreset

if TYPE_CHECKING:
    from ..models import Resolution


# Bitrate ladder matching Plex/Jellyfin quality
QUALITY_LADDER: List[QualityPreset] = [
    QualityPreset("4K", 3840, 2160, "20M", "384k", 18, "p4"),
    QualityPreset("4K-low", 3840, 2160, "12M", "256k", 20, "p5"),
    QualityPreset("1080p", 1920, 1080, "8M", "192k", 20, "p4"),
    QualityPreset("1080p-low", 1920, 1080, "4M", "128k", 23, "p5"),
    QualityPreset("720p", 1280, 720, "4M", "128k", 22, "p4"),
    QualityPreset("720p-low", 1280, 720, "2M", "96k", 24, "p5"),
    QualityPreset("480p", 854, 480, "1.5M", "96k", 24, "p5"),
    QualityPreset("360p", 640, 360, "800k", "64k", 26, "p6"),
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

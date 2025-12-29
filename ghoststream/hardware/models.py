"""
Hardware detection models and data classes for GhostStream
"""

from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field, asdict
from enum import Enum


class HWAccelType(str, Enum):
    NVENC = "nvenc"
    QSV = "qsv"
    VAAPI = "vaapi"
    AMF = "amf"
    VIDEOTOOLBOX = "videotoolbox"
    SOFTWARE = "software"


@dataclass
class GPUInfo:
    name: str
    memory_mb: int = 0
    driver_version: str = ""
    cuda_version: str = ""


@dataclass
class HWAccelCapability:
    type: HWAccelType
    available: bool
    encoders: List[str] = field(default_factory=list)
    decoders: List[str] = field(default_factory=list)
    gpu_info: Optional[GPUInfo] = None
    device_path: Optional[str] = None  # For VAAPI: discovered render device path

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "type": self.type.value,
            "available": self.available,
            "encoders": self.encoders,
            "decoders": self.decoders,
        }
        if self.gpu_info:
            result["gpu_info"] = asdict(self.gpu_info)
        if self.device_path:
            result["device_path"] = self.device_path
        return result


@dataclass
class Capabilities:
    hw_accels: List[HWAccelCapability] = field(default_factory=list)
    video_codecs: List[str] = field(default_factory=list)
    audio_codecs: List[str] = field(default_factory=list)
    formats: List[str] = field(default_factory=list)
    max_concurrent_jobs: int = 2
    ffmpeg_version: str = ""
    platform: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "hw_accels": [h.to_dict() for h in self.hw_accels],
            "video_codecs": self.video_codecs,
            "audio_codecs": self.audio_codecs,
            "formats": self.formats,
            "max_concurrent_jobs": self.max_concurrent_jobs,
            "ffmpeg_version": self.ffmpeg_version,
            "platform": self.platform,
        }
    
    def get_best_hw_accel(self) -> HWAccelType:
        """Return the best available hardware acceleration based on detected hardware."""
        import platform as plat

        # Get available acceleration types
        available_types = {hw.type for hw in self.hw_accels if hw.available}

        if not available_types:
            return HWAccelType.SOFTWARE

        system = plat.system()

        if system == "Darwin":
            # macOS: VideoToolbox is the only option
            if HWAccelType.VIDEOTOOLBOX in available_types:
                return HWAccelType.VIDEOTOOLBOX

        elif system == "Windows":
            # Windows: Prefer vendor of detected GPU
            # NVENC > AMF > QSV (discrete GPUs typically faster than iGPU)
            if HWAccelType.NVENC in available_types:
                return HWAccelType.NVENC
            if HWAccelType.AMF in available_types:
                return HWAccelType.AMF
            if HWAccelType.QSV in available_types:
                return HWAccelType.QSV

        else:  # Linux
            # Linux: NVENC > VAAPI > QSV
            # VAAPI works for both AMD and Intel on Linux
            if HWAccelType.NVENC in available_types:
                return HWAccelType.NVENC
            if HWAccelType.VAAPI in available_types:
                return HWAccelType.VAAPI
            if HWAccelType.QSV in available_types:
                return HWAccelType.QSV

        return HWAccelType.SOFTWARE

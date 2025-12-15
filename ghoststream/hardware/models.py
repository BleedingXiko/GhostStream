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
    
    def to_dict(self) -> Dict[str, Any]:
        result = {
            "type": self.type.value,
            "available": self.available,
            "encoders": self.encoders,
            "decoders": self.decoders,
        }
        if self.gpu_info:
            result["gpu_info"] = asdict(self.gpu_info)
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
        """Return the best available hardware acceleration."""
        priority = [
            HWAccelType.NVENC,
            HWAccelType.QSV,
            HWAccelType.VIDEOTOOLBOX,
            HWAccelType.VAAPI,
            HWAccelType.AMF,
        ]
        for accel_type in priority:
            for hw in self.hw_accels:
                if hw.type == accel_type and hw.available:
                    return accel_type
        return HWAccelType.SOFTWARE

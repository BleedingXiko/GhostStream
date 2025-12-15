"""
Hardware detection package for GhostStream
"""

from .models import (
    HWAccelType,
    GPUInfo,
    HWAccelCapability,
    Capabilities,
)
from .detector import (
    HardwareDetector,
    get_capabilities,
)

__all__ = [
    "HWAccelType",
    "GPUInfo",
    "HWAccelCapability",
    "Capabilities",
    "HardwareDetector",
    "get_capabilities",
]

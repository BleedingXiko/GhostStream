"""
Hardware acceleration detection for GhostStream
"""

import subprocess
import platform
import shutil
import re
from typing import Dict, List, Optional
import logging

from .models import (
    HWAccelType, GPUInfo, HWAccelCapability, Capabilities
)

logger = logging.getLogger(__name__)


class HardwareDetector:
    """Detects available hardware acceleration capabilities."""
    
    def __init__(self, ffmpeg_path: str = "auto"):
        self.ffmpeg_path = self._find_ffmpeg(ffmpeg_path)
        self.ffprobe_path = self._find_ffprobe()
        
    def _find_ffmpeg(self, path: str) -> str:
        """Find ffmpeg executable."""
        if path != "auto" and shutil.which(path):
            return path
        
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg:
            return ffmpeg
        
        # Common installation paths
        common_paths = [
            "/usr/bin/ffmpeg",
            "/usr/local/bin/ffmpeg",
            "/opt/homebrew/bin/ffmpeg",
            "C:\\ffmpeg\\bin\\ffmpeg.exe",
            "C:\\Program Files\\ffmpeg\\bin\\ffmpeg.exe",
        ]
        
        for p in common_paths:
            if shutil.which(p):
                return p
        
        raise RuntimeError("FFmpeg not found. Please install FFmpeg or specify the path in config.")
    
    def _find_ffprobe(self) -> str:
        """Find ffprobe executable."""
        ffprobe = shutil.which("ffprobe")
        if ffprobe:
            return ffprobe
        
        # Try same directory as ffmpeg
        ffmpeg_dir = str(self.ffmpeg_path).rsplit("/", 1)[0] if "/" in self.ffmpeg_path else str(self.ffmpeg_path).rsplit("\\", 1)[0]
        ffprobe_path = f"{ffmpeg_dir}/ffprobe"
        if shutil.which(ffprobe_path):
            return ffprobe_path
        
        return "ffprobe"
    
    def _run_command(self, cmd: List[str], timeout: int = 10) -> tuple[int, str, str]:
        """Run a command and return exit code, stdout, stderr."""
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            return -1, "", "Command timed out"
        except FileNotFoundError:
            return -1, "", "Command not found"
        except Exception as e:
            return -1, "", str(e)
    
    def get_ffmpeg_version(self) -> str:
        """Get FFmpeg version string."""
        code, stdout, stderr = self._run_command([self.ffmpeg_path, "-version"])
        if code == 0:
            first_line = stdout.split("\n")[0]
            match = re.search(r"ffmpeg version (\S+)", first_line)
            if match:
                return match.group(1)
        return "unknown"
    
    def get_ffmpeg_encoders(self) -> Dict[str, List[str]]:
        """Get list of available encoders from FFmpeg."""
        code, stdout, _ = self._run_command([self.ffmpeg_path, "-encoders", "-hide_banner"])
        
        video_encoders = []
        audio_encoders = []
        
        if code == 0:
            for line in stdout.split("\n"):
                line = line.strip()
                if line.startswith("V"):
                    parts = line.split()
                    if len(parts) >= 2:
                        video_encoders.append(parts[1])
                elif line.startswith("A"):
                    parts = line.split()
                    if len(parts) >= 2:
                        audio_encoders.append(parts[1])
        
        return {"video": video_encoders, "audio": audio_encoders}
    
    def get_ffmpeg_decoders(self) -> Dict[str, List[str]]:
        """Get list of available decoders from FFmpeg."""
        code, stdout, _ = self._run_command([self.ffmpeg_path, "-decoders", "-hide_banner"])
        
        video_decoders = []
        audio_decoders = []
        
        if code == 0:
            for line in stdout.split("\n"):
                line = line.strip()
                if line.startswith("V"):
                    parts = line.split()
                    if len(parts) >= 2:
                        video_decoders.append(parts[1])
                elif line.startswith("A"):
                    parts = line.split()
                    if len(parts) >= 2:
                        audio_decoders.append(parts[1])
        
        return {"video": video_decoders, "audio": audio_decoders}
    
    def detect_nvidia(self) -> HWAccelCapability:
        """Detect NVIDIA NVENC support."""
        capability = HWAccelCapability(type=HWAccelType.NVENC, available=False)
        
        # Check for nvidia-smi
        code, stdout, _ = self._run_command(["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"])
        
        if code != 0:
            logger.debug("NVIDIA GPU not detected (nvidia-smi not available)")
            return capability
        
        # Parse GPU info
        try:
            parts = stdout.strip().split(", ")
            if len(parts) >= 3:
                capability.gpu_info = GPUInfo(
                    name=parts[0],
                    memory_mb=int(float(parts[1])),
                    driver_version=parts[2]
                )
        except Exception as e:
            logger.debug(f"Failed to parse nvidia-smi output: {e}")
        
        # Check for CUDA version
        code, stdout, _ = self._run_command(["nvidia-smi", "--query-gpu=compute_cap", "--format=csv,noheader"])
        if code == 0 and capability.gpu_info:
            capability.gpu_info.cuda_version = stdout.strip()
        
        # Check FFmpeg for NVENC encoders
        encoders = self.get_ffmpeg_encoders()
        nvenc_encoders = [e for e in encoders.get("video", []) if "nvenc" in e.lower()]
        
        if nvenc_encoders:
            capability.available = True
            capability.encoders = nvenc_encoders
        
        # Check for NVDEC decoders
        decoders = self.get_ffmpeg_decoders()
        nvdec_decoders = [d for d in decoders.get("video", []) if "cuvid" in d.lower()]
        capability.decoders = nvdec_decoders
        
        logger.info(f"NVIDIA NVENC: {'Available' if capability.available else 'Not available'}")
        return capability
    
    def detect_intel_qsv(self) -> HWAccelCapability:
        """Detect Intel QuickSync support."""
        capability = HWAccelCapability(type=HWAccelType.QSV, available=False)
        
        encoders = self.get_ffmpeg_encoders()
        qsv_encoders = [e for e in encoders.get("video", []) if "qsv" in e.lower()]
        
        if qsv_encoders:
            # Verify QSV is actually working by attempting to initialize
            code, _, stderr = self._run_command([
                self.ffmpeg_path, "-hide_banner", "-init_hw_device", "qsv=qsv",
                "-f", "lavfi", "-i", "nullsrc=s=256x256:d=1", "-frames:v", "1",
                "-c:v", "h264_qsv", "-f", "null", "-"
            ])
            
            if code == 0 or "Error" not in stderr:
                capability.available = True
                capability.encoders = qsv_encoders
        
        decoders = self.get_ffmpeg_decoders()
        qsv_decoders = [d for d in decoders.get("video", []) if "qsv" in d.lower()]
        capability.decoders = qsv_decoders
        
        logger.info(f"Intel QuickSync: {'Available' if capability.available else 'Not available'}")
        return capability
    
    def detect_vaapi(self) -> HWAccelCapability:
        """Detect VA-API support (Linux)."""
        capability = HWAccelCapability(type=HWAccelType.VAAPI, available=False)
        
        if platform.system() != "Linux":
            return capability
        
        encoders = self.get_ffmpeg_encoders()
        vaapi_encoders = [e for e in encoders.get("video", []) if "vaapi" in e.lower()]
        
        if vaapi_encoders:
            # Check if VA-API device exists
            import os
            if os.path.exists("/dev/dri/renderD128"):
                capability.available = True
                capability.encoders = vaapi_encoders
        
        decoders = self.get_ffmpeg_decoders()
        vaapi_decoders = [d for d in decoders.get("video", []) if "vaapi" in d.lower()]
        capability.decoders = vaapi_decoders
        
        logger.info(f"VA-API: {'Available' if capability.available else 'Not available'}")
        return capability
    
    def detect_amd_amf(self) -> HWAccelCapability:
        """Detect AMD AMF support."""
        capability = HWAccelCapability(type=HWAccelType.AMF, available=False)
        
        encoders = self.get_ffmpeg_encoders()
        amf_encoders = [e for e in encoders.get("video", []) if "amf" in e.lower()]
        
        if amf_encoders:
            capability.available = True
            capability.encoders = amf_encoders
        
        logger.info(f"AMD AMF: {'Available' if capability.available else 'Not available'}")
        return capability
    
    def detect_videotoolbox(self) -> HWAccelCapability:
        """Detect Apple VideoToolbox support (macOS)."""
        capability = HWAccelCapability(type=HWAccelType.VIDEOTOOLBOX, available=False)
        
        if platform.system() != "Darwin":
            return capability
        
        encoders = self.get_ffmpeg_encoders()
        vt_encoders = [e for e in encoders.get("video", []) if "videotoolbox" in e.lower()]
        
        if vt_encoders:
            capability.available = True
            capability.encoders = vt_encoders
        
        decoders = self.get_ffmpeg_decoders()
        vt_decoders = [d for d in decoders.get("video", []) if "videotoolbox" in d.lower()]
        capability.decoders = vt_decoders
        
        logger.info(f"VideoToolbox: {'Available' if capability.available else 'Not available'}")
        return capability
    
    def detect_software(self) -> HWAccelCapability:
        """Detect software encoding support (always available)."""
        capability = HWAccelCapability(type=HWAccelType.SOFTWARE, available=True)
        
        encoders = self.get_ffmpeg_encoders()
        
        # Common software encoders
        software_encoders = []
        for enc in ["libx264", "libx265", "libvpx", "libvpx-vp9", "libaom-av1", "libsvtav1"]:
            if enc in encoders.get("video", []):
                software_encoders.append(enc)
        
        capability.encoders = software_encoders
        
        return capability
    
    def detect_all(self, max_concurrent_jobs: int = 2) -> Capabilities:
        """Detect all hardware acceleration capabilities."""
        capabilities = Capabilities(
            platform=f"{platform.system()} {platform.release()}",
            ffmpeg_version=self.get_ffmpeg_version(),
            max_concurrent_jobs=max_concurrent_jobs,
        )
        
        # Detect hardware acceleration
        capabilities.hw_accels = [
            self.detect_nvidia(),
            self.detect_intel_qsv(),
            self.detect_vaapi(),
            self.detect_amd_amf(),
            self.detect_videotoolbox(),
            self.detect_software(),
        ]
        
        # Get supported codecs
        encoders = self.get_ffmpeg_encoders()
        
        # Video codecs
        codec_mapping = {
            "h264": ["libx264", "h264_nvenc", "h264_qsv", "h264_vaapi", "h264_amf", "h264_videotoolbox"],
            "h265": ["libx265", "hevc_nvenc", "hevc_qsv", "hevc_vaapi", "hevc_amf", "hevc_videotoolbox"],
            "vp9": ["libvpx-vp9", "vp9_vaapi", "vp9_qsv"],
            "av1": ["libaom-av1", "libsvtav1", "av1_nvenc", "av1_qsv", "av1_vaapi"],
        }
        
        for codec, encoder_list in codec_mapping.items():
            for enc in encoder_list:
                if enc in encoders.get("video", []):
                    if codec not in capabilities.video_codecs:
                        capabilities.video_codecs.append(codec)
                    break
        
        # Audio codecs
        audio_codec_mapping = {
            "aac": ["aac", "libfdk_aac"],
            "opus": ["libopus"],
            "mp3": ["libmp3lame"],
            "flac": ["flac"],
            "ac3": ["ac3"],
            "eac3": ["eac3"],
        }
        
        for codec, encoder_list in audio_codec_mapping.items():
            for enc in encoder_list:
                if enc in encoders.get("audio", []):
                    if codec not in capabilities.audio_codecs:
                        capabilities.audio_codecs.append(codec)
                    break
        
        # Always support "copy" for stream copying
        capabilities.audio_codecs.append("copy")
        
        # Supported output formats
        capabilities.formats = ["hls", "dash", "mp4", "webm", "mkv"]
        
        return capabilities


# Global capabilities cache
_capabilities: Optional[Capabilities] = None


def get_capabilities(ffmpeg_path: str = "auto", max_concurrent_jobs: int = 2, force_refresh: bool = False) -> Capabilities:
    """Get cached hardware capabilities or detect them."""
    global _capabilities
    
    if _capabilities is None or force_refresh:
        detector = HardwareDetector(ffmpeg_path)
        _capabilities = detector.detect_all(max_concurrent_jobs)
    
    return _capabilities

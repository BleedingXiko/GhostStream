"""
Encoder selection logic for video and audio codecs.
Handles hardware acceleration detection and fallback.
"""

import logging
from typing import List, Tuple

from ..models import VideoCodec, AudioCodec, HWAccel
from ..hardware import HWAccelType, Capabilities
from ..config import HardwareConfig

logger = logging.getLogger(__name__)


class EncoderSelector:
    """Selects appropriate encoders based on codec and hardware capabilities."""
    
    def __init__(self, capabilities: Capabilities, hw_config: HardwareConfig):
        self.capabilities = capabilities
        self.hw_config = hw_config
        self._failed_encoders: set = set()  # Track encoders that have failed
    
    def get_video_encoder(
        self,
        codec: VideoCodec,
        hw_accel: HWAccel
    ) -> Tuple[str, List[str]]:
        """Get the video encoder and extra args based on codec and hw acceleration."""
        
        # Determine which hw accel to use
        if hw_accel == HWAccel.AUTO:
            best_accel = self.capabilities.get_best_hw_accel()
        else:
            accel_map = {
                HWAccel.NVENC: HWAccelType.NVENC,
                HWAccel.QSV: HWAccelType.QSV,
                HWAccel.VAAPI: HWAccelType.VAAPI,
                HWAccel.VIDEOTOOLBOX: HWAccelType.VIDEOTOOLBOX,
                HWAccel.AMF: HWAccelType.AMF,
                HWAccel.SOFTWARE: HWAccelType.SOFTWARE,
            }
            best_accel = accel_map.get(hw_accel, HWAccelType.SOFTWARE)
        
        # Check if requested hw accel is available
        hw_available = False
        for hw in self.capabilities.hw_accels:
            if hw.type == best_accel and hw.available:
                hw_available = True
                break
        
        if not hw_available and self.hw_config.fallback_to_software:
            best_accel = HWAccelType.SOFTWARE
        
        if codec == VideoCodec.COPY:
            return "copy", []
        
        # Map codec to encoder based on hw accel
        encoder_map = self._get_encoder_map(codec)
        
        # Handle codecs with limited hw support
        if codec in (VideoCodec.VP9, VideoCodec.AV1):
            if best_accel not in encoder_map:
                best_accel = HWAccelType.SOFTWARE
        
        encoder, extra_args = encoder_map.get(
            best_accel,
            encoder_map.get(HWAccelType.SOFTWARE, ("libx264", ["-preset", "medium", "-crf", "23"]))
        )
        
        return encoder, extra_args
    
    def _get_encoder_map(self, codec: VideoCodec) -> dict:
        """Get encoder mapping for a specific codec."""
        config = self.hw_config
        
        if codec == VideoCodec.H264:
            return {
                HWAccelType.NVENC: ("h264_nvenc", ["-preset", config.nvenc_preset]),
                HWAccelType.QSV: ("h264_qsv", ["-preset", config.qsv_preset]),
                HWAccelType.VAAPI: ("h264_vaapi", []),
                HWAccelType.VIDEOTOOLBOX: ("h264_videotoolbox", []),
                HWAccelType.AMF: ("h264_amf", []),
                HWAccelType.SOFTWARE: ("libx264", ["-preset", "medium", "-crf", "23"]),
            }
        elif codec == VideoCodec.H265:
            return {
                HWAccelType.NVENC: ("hevc_nvenc", ["-preset", config.nvenc_preset]),
                HWAccelType.QSV: ("hevc_qsv", ["-preset", config.qsv_preset]),
                HWAccelType.VAAPI: ("hevc_vaapi", []),
                HWAccelType.VIDEOTOOLBOX: ("hevc_videotoolbox", []),
                HWAccelType.AMF: ("hevc_amf", []),
                HWAccelType.SOFTWARE: ("libx265", ["-preset", "medium", "-crf", "28"]),
            }
        elif codec == VideoCodec.VP9:
            return {
                HWAccelType.VAAPI: ("vp9_vaapi", []),
                HWAccelType.QSV: ("vp9_qsv", []),
                HWAccelType.SOFTWARE: ("libvpx-vp9", ["-cpu-used", "4", "-crf", "30", "-b:v", "0"]),
            }
        elif codec == VideoCodec.AV1:
            return {
                HWAccelType.NVENC: ("av1_nvenc", ["-preset", config.nvenc_preset]),
                HWAccelType.QSV: ("av1_qsv", ["-preset", config.qsv_preset]),
                HWAccelType.VAAPI: ("av1_vaapi", []),
                HWAccelType.SOFTWARE: ("libsvtav1", ["-preset", "6", "-crf", "30"]),
            }
        else:
            return {
                HWAccelType.SOFTWARE: ("libx264", ["-preset", "medium", "-crf", "23"]),
            }
    
    def get_audio_encoder(self, codec: AudioCodec) -> Tuple[str, List[str]]:
        """Get the audio encoder based on codec."""
        if codec == AudioCodec.COPY:
            return "copy", []
        
        encoder_map = {
            AudioCodec.AAC: ("aac", ["-b:a", "192k"]),
            AudioCodec.OPUS: ("libopus", ["-b:a", "128k"]),
            AudioCodec.MP3: ("libmp3lame", ["-b:a", "192k"]),
            AudioCodec.FLAC: ("flac", []),
            AudioCodec.AC3: ("ac3", ["-b:a", "384k"]),
        }
        
        return encoder_map.get(codec, ("aac", ["-b:a", "192k"]))
    
    def get_hw_decode_args(
        self,
        video_encoder: str,
        vaapi_device: str = "/dev/dri/renderD128"
    ) -> Tuple[List[str], str | None]:
        """Get hardware decoding arguments based on encoder."""
        if "nvenc" in video_encoder:
            return ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"], "cuda"
        elif "qsv" in video_encoder:
            return ["-hwaccel", "qsv", "-hwaccel_output_format", "qsv"], "qsv"
        elif "vaapi" in video_encoder:
            return [
                "-hwaccel", "vaapi",
                "-hwaccel_device", vaapi_device,
                "-hwaccel_output_format", "vaapi"
            ], "vaapi"
        elif "videotoolbox" in video_encoder:
            return ["-hwaccel", "videotoolbox"], "videotoolbox"
        elif "amf" in video_encoder:
            return ["-hwaccel", "d3d11va"], "amf"
        return [], None
    
    def detect_hw_accel_used(self, encoder: str) -> str:
        """Determine which hardware acceleration was used."""
        if "nvenc" in encoder:
            return "nvenc"
        elif "qsv" in encoder:
            return "qsv"
        elif "vaapi" in encoder:
            return "vaapi"
        elif "videotoolbox" in encoder:
            return "videotoolbox"
        elif "amf" in encoder:
            return "amf"
        return "software"
    
    def is_hw_error(self, error_msg: str) -> bool:
        """Check if error is related to hardware encoding failure."""
        hw_errors = [
            "no capable devices found",
            "cannot open",
            "initialization failed",
            "not available",
            "driver",
            "cuda",
            "nvenc",
            "qsv",
            "vaapi",
            "device",
            "gpu",
            "hw_frames_ctx",
            "hwaccel",
        ]
        error_lower = error_msg.lower()
        return any(err in error_lower for err in hw_errors)
    
    def mark_hw_failed(self, encoder: str) -> None:
        """
        Mark a hardware encoder as failed.
        
        This prevents the encoder from being selected again until reset.
        Also updates the capabilities to reflect the failure.
        """
        self._failed_encoders.add(encoder)
        logger.warning(f"[Encoder] Marked encoder as failed: {encoder}")
        
        # Update capabilities to mark the corresponding hw accel as unavailable
        hw_type = self._encoder_to_hw_type(encoder)
        if hw_type:
            for hw in self.capabilities.hw_accels:
                if hw.type == hw_type:
                    hw.available = False
                    logger.info(f"[Encoder] Disabled {hw_type.value} hardware acceleration")
                    break
    
    def _encoder_to_hw_type(self, encoder: str) -> HWAccelType | None:
        """Map encoder name to hardware acceleration type."""
        if "nvenc" in encoder:
            return HWAccelType.NVENC
        elif "qsv" in encoder:
            return HWAccelType.QSV
        elif "vaapi" in encoder:
            return HWAccelType.VAAPI
        elif "videotoolbox" in encoder:
            return HWAccelType.VIDEOTOOLBOX
        elif "amf" in encoder:
            return HWAccelType.AMF
        return None
    
    def is_encoder_failed(self, encoder: str) -> bool:
        """Check if an encoder has been marked as failed."""
        return encoder in self._failed_encoders
    
    def reset_failed_encoders(self) -> None:
        """Reset the list of failed encoders (e.g., after system restart)."""
        self._failed_encoders.clear()
        logger.info("[Encoder] Reset failed encoders list")

"""
Encoder selection logic for video and audio codecs.
Handles hardware acceleration detection and fallback.
"""

import logging
import time
from typing import Dict, List, Optional, Tuple

from ..models import VideoCodec, AudioCodec, HWAccel
from ..hardware import HWAccelType, Capabilities
from ..config import HardwareConfig

logger = logging.getLogger(__name__)


# =============================================================================
# ENCODER QUALITY SETTINGS
# =============================================================================
# These settings prevent artifacts and ensure high-quality encoding

# NVENC quality tuning - prevents blocking, banding, and scene change artifacts
NVENC_QUALITY_ARGS = {
    "p4": [  # Balanced quality/speed (default)
        "-preset", "p4",
        "-tune", "hq",
        "-rc", "vbr",
        "-multipass", "qres",
        "-spatial-aq", "1",
        "-temporal-aq", "1",
        "-aq-strength", "8",
        "-rc-lookahead", "32",
        "-bf", "3",
        "-b_ref_mode", "middle",
    ],
    "p5": [  # Quality priority
        "-preset", "p5",
        "-tune", "hq",
        "-rc", "vbr",
        "-multipass", "qres",
        "-spatial-aq", "1",
        "-temporal-aq", "1",
        "-aq-strength", "10",
        "-rc-lookahead", "32",
        "-bf", "4",
        "-b_ref_mode", "middle",
    ],
    "p6": [  # Speed priority (low res)
        "-preset", "p6",
        "-tune", "ll",
        "-rc", "vbr",
        "-spatial-aq", "1",
        "-aq-strength", "8",
        "-bf", "0",
    ],
}

# QSV quality tuning - Intel QuickSync
QSV_QUALITY_ARGS = {
    "medium": [
        "-preset", "medium",
        "-look_ahead", "1",
        "-look_ahead_depth", "40",
        "-extbrc", "1",
        "-b_strategy", "1",
        "-bf", "3",
    ],
    "fast": [
        "-preset", "fast",
        "-look_ahead", "1",
        "-look_ahead_depth", "20",
        "-bf", "2",
    ],
    "slow": [
        "-preset", "slow",
        "-look_ahead", "1",
        "-look_ahead_depth", "60",
        "-extbrc", "1",
        "-b_strategy", "1",
        "-bf", "4",
    ],
}

# AMF quality tuning - AMD
AMF_QUALITY_ARGS = [
    "-quality", "quality",
    "-rc", "vbr_latency",
    "-enforce_hrd", "1",
    "-vbaq", "1",
    "-preanalysis", "1",
    "-bf", "3",
]

# VAAPI quality tuning
VAAPI_QUALITY_ARGS = [
    "-rc_mode", "VBR",
    "-bf", "3",
]

# VideoToolbox quality tuning - Apple
VIDEOTOOLBOX_QUALITY_ARGS = [
    "-realtime", "0",
    "-allow_sw", "0",
]


class EncoderSelector:
    """Selects appropriate encoders based on codec and hardware capabilities."""
    
    def __init__(self, capabilities: Capabilities, hw_config: HardwareConfig):
        self.capabilities = capabilities
        self.hw_config = hw_config
        self._failed_encoders: set = set()  # Track encoders that have failed
        self._failure_counts: Dict[str, int] = {}  # Track failure count per encoder
        self._last_failure_time: Dict[str, float] = {}  # Track when failure occurred
        self._cooldown_seconds = 300  # 5 minute cooldown before retry
    
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
        """Get encoder mapping for a specific codec with full quality args."""
        config = self.hw_config

        # Get quality args for each encoder type
        nvenc_args = NVENC_QUALITY_ARGS.get(config.nvenc_preset, NVENC_QUALITY_ARGS["p4"])
        qsv_args = QSV_QUALITY_ARGS.get(config.qsv_preset, QSV_QUALITY_ARGS["medium"])

        if codec == VideoCodec.H264:
            return {
                HWAccelType.NVENC: ("h264_nvenc", nvenc_args.copy()),
                HWAccelType.QSV: ("h264_qsv", qsv_args.copy()),
                HWAccelType.VAAPI: ("h264_vaapi", VAAPI_QUALITY_ARGS.copy()),
                HWAccelType.VIDEOTOOLBOX: ("h264_videotoolbox", VIDEOTOOLBOX_QUALITY_ARGS.copy()),
                HWAccelType.AMF: ("h264_amf", AMF_QUALITY_ARGS.copy()),
                HWAccelType.SOFTWARE: ("libx264", [
                    "-preset", "medium",
                    "-tune", "film",
                    "-profile:v", "high",
                    "-rc-lookahead", "40",
                    "-bf", "3",
                    "-aq-mode", "2",
                ]),
            }
        elif codec == VideoCodec.H265:
            return {
                HWAccelType.NVENC: ("hevc_nvenc", nvenc_args.copy()),
                HWAccelType.QSV: ("hevc_qsv", qsv_args.copy()),
                HWAccelType.VAAPI: ("hevc_vaapi", VAAPI_QUALITY_ARGS.copy()),
                HWAccelType.VIDEOTOOLBOX: ("hevc_videotoolbox", VIDEOTOOLBOX_QUALITY_ARGS.copy()),
                HWAccelType.AMF: ("hevc_amf", AMF_QUALITY_ARGS.copy()),
                HWAccelType.SOFTWARE: ("libx265", [
                    "-preset", "medium",
                    "-x265-params", "aq-mode=2:rc-lookahead=20",
                ]),
            }
        elif codec == VideoCodec.VP9:
            return {
                HWAccelType.VAAPI: ("vp9_vaapi", VAAPI_QUALITY_ARGS.copy()),
                HWAccelType.QSV: ("vp9_qsv", qsv_args.copy()),
                HWAccelType.SOFTWARE: ("libvpx-vp9", [
                    "-cpu-used", "4",
                    "-crf", "30",
                    "-b:v", "0",
                    "-row-mt", "1",
                ]),
            }
        elif codec == VideoCodec.AV1:
            return {
                HWAccelType.NVENC: ("av1_nvenc", nvenc_args.copy()),
                HWAccelType.QSV: ("av1_qsv", qsv_args.copy()),
                HWAccelType.VAAPI: ("av1_vaapi", VAAPI_QUALITY_ARGS.copy()),
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
    
    def mark_hw_failed(self, encoder: str, job_id: Optional[str] = None) -> None:
        """
        Mark a hardware encoder as failed with cooldown-based recovery.

        Uses exponential backoff instead of permanently disabling.
        Only disables globally after multiple consecutive failures.
        """
        self._failure_counts[encoder] = self._failure_counts.get(encoder, 0) + 1
        self._last_failure_time[encoder] = time.time()

        failures = self._failure_counts[encoder]
        logger.warning(f"[Encoder] Encoder {encoder} failed (count: {failures})")

        # Only disable globally after multiple consecutive failures (3+)
        if failures >= 3:
            self._failed_encoders.add(encoder)
            hw_type = self._encoder_to_hw_type(encoder)
            if hw_type:
                for hw in self.capabilities.hw_accels:
                    if hw.type == hw_type:
                        hw.available = False
                        logger.warning(f"[Encoder] Disabled {hw_type.value} after {failures} failures")
                        break

    def is_encoder_available(self, encoder: str) -> bool:
        """Check if encoder is available (considering cooldown)."""
        if encoder not in self._failed_encoders:
            return True

        # Check if cooldown has passed
        last_failure = self._last_failure_time.get(encoder, 0)
        failures = self._failure_counts.get(encoder, 0)

        # Exponential backoff: 5 min, 10 min, 20 min, 40 min, etc.
        cooldown = self._cooldown_seconds * (2 ** (failures - 3))  # Start at 5 min after 3 failures
        cooldown = min(cooldown, 3600)  # Cap at 1 hour

        if time.time() - last_failure > cooldown:
            # Reset and allow retry
            self._failed_encoders.discard(encoder)
            logger.info(f"[Encoder] Re-enabling {encoder} after {cooldown}s cooldown")

            # Re-enable hw accel capability
            hw_type = self._encoder_to_hw_type(encoder)
            if hw_type:
                for hw in self.capabilities.hw_accels:
                    if hw.type == hw_type:
                        hw.available = True
                        break
            return True

        return False

    def reset_encoder(self, encoder: str) -> None:
        """Reset failure state for a specific encoder (e.g., after successful encode)."""
        self._failed_encoders.discard(encoder)
        self._failure_counts.pop(encoder, None)
        self._last_failure_time.pop(encoder, None)

        hw_type = self._encoder_to_hw_type(encoder)
        if hw_type:
            for hw in self.capabilities.hw_accels:
                if hw.type == hw_type:
                    hw.available = True
                    break
        logger.debug(f"[Encoder] Reset failure state for {encoder}")
    
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

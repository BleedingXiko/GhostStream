"""
Tests for GhostStream Encoder Selector.

Tests cover:
- Encoder selection based on hardware
- Hardware fallback tracking
- mark_hw_failed functionality
- Encoder-to-hardware type mapping
"""

import pytest
from unittest.mock import MagicMock

from ghoststream.transcoding.encoders import EncoderSelector
from ghoststream.hardware import Capabilities, HWAccelType, HWAccelCapability, GPUInfo
from ghoststream.config import HardwareConfig
from ghoststream.models import VideoCodec, AudioCodec, HWAccel


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def capabilities_nvenc():
    """Create capabilities with NVENC available."""
    return Capabilities(
        hw_accels=[
            HWAccelCapability(
                type=HWAccelType.NVENC,
                available=True,
                encoders=["h264_nvenc", "hevc_nvenc"],
                gpu_info=GPUInfo(name="RTX 3080", memory_mb=10000)
            ),
            HWAccelCapability(
                type=HWAccelType.SOFTWARE,
                available=True,
                encoders=["libx264", "libx265"]
            )
        ]
    )


@pytest.fixture
def capabilities_qsv():
    """Create capabilities with QSV available."""
    return Capabilities(
        hw_accels=[
            HWAccelCapability(
                type=HWAccelType.QSV,
                available=True,
                encoders=["h264_qsv", "hevc_qsv"]
            ),
            HWAccelCapability(
                type=HWAccelType.SOFTWARE,
                available=True,
                encoders=["libx264", "libx265"]
            )
        ]
    )


@pytest.fixture
def capabilities_software():
    """Create capabilities with software only."""
    return Capabilities(
        hw_accels=[
            HWAccelCapability(
                type=HWAccelType.SOFTWARE,
                available=True,
                encoders=["libx264", "libx265", "libvpx-vp9"]
            )
        ]
    )


@pytest.fixture
def hw_config():
    """Create default hardware config."""
    return HardwareConfig()


# =============================================================================
# ENCODER SELECTION TESTS
# =============================================================================

class TestEncoderSelection:
    """Tests for encoder selection logic."""
    
    def test_select_nvenc_h264(self, capabilities_nvenc, hw_config):
        """Should select h264_nvenc when available."""
        selector = EncoderSelector(capabilities_nvenc, hw_config)
        encoder, args = selector.get_video_encoder(VideoCodec.H264, HWAccel.AUTO)
        
        assert encoder == "h264_nvenc"
        assert "-preset" in args
    
    def test_select_nvenc_h265(self, capabilities_nvenc, hw_config):
        """Should select hevc_nvenc when available."""
        selector = EncoderSelector(capabilities_nvenc, hw_config)
        encoder, args = selector.get_video_encoder(VideoCodec.H265, HWAccel.AUTO)
        
        assert encoder == "hevc_nvenc"
    
    def test_select_qsv_h264(self, capabilities_qsv, hw_config):
        """Should select h264_qsv when available."""
        selector = EncoderSelector(capabilities_qsv, hw_config)
        encoder, args = selector.get_video_encoder(VideoCodec.H264, HWAccel.AUTO)
        
        assert encoder == "h264_qsv"
    
    def test_select_software_fallback(self, capabilities_software, hw_config):
        """Should fallback to software encoder."""
        selector = EncoderSelector(capabilities_software, hw_config)
        encoder, args = selector.get_video_encoder(VideoCodec.H264, HWAccel.AUTO)
        
        assert encoder == "libx264"
        assert "-preset" in args
        assert "-crf" in args
    
    def test_force_software(self, capabilities_nvenc, hw_config):
        """Should use software when explicitly requested."""
        selector = EncoderSelector(capabilities_nvenc, hw_config)
        encoder, args = selector.get_video_encoder(VideoCodec.H264, HWAccel.SOFTWARE)
        
        assert encoder == "libx264"
    
    def test_copy_codec(self, capabilities_nvenc, hw_config):
        """Should return copy for COPY codec."""
        selector = EncoderSelector(capabilities_nvenc, hw_config)
        encoder, args = selector.get_video_encoder(VideoCodec.COPY, HWAccel.AUTO)
        
        assert encoder == "copy"
        assert args == []


# =============================================================================
# AUDIO ENCODER TESTS
# =============================================================================

class TestAudioEncoderSelection:
    """Tests for audio encoder selection."""
    
    def test_select_aac(self, capabilities_software, hw_config):
        """Should select AAC encoder."""
        selector = EncoderSelector(capabilities_software, hw_config)
        encoder, args = selector.get_audio_encoder(AudioCodec.AAC)
        
        assert encoder == "aac"
    
    def test_select_opus(self, capabilities_software, hw_config):
        """Should select Opus encoder."""
        selector = EncoderSelector(capabilities_software, hw_config)
        encoder, args = selector.get_audio_encoder(AudioCodec.OPUS)
        
        assert encoder == "libopus"
    
    def test_select_copy(self, capabilities_software, hw_config):
        """Should return copy for COPY codec."""
        selector = EncoderSelector(capabilities_software, hw_config)
        encoder, args = selector.get_audio_encoder(AudioCodec.COPY)
        
        assert encoder == "copy"
        assert args == []


# =============================================================================
# HARDWARE DECODE ARGS TESTS
# =============================================================================

class TestHWDecodeArgs:
    """Tests for hardware decoding arguments."""
    
    def test_nvenc_decode_args(self, capabilities_nvenc, hw_config):
        """Should return CUDA decode args for NVENC."""
        selector = EncoderSelector(capabilities_nvenc, hw_config)
        args, hw_type = selector.get_hw_decode_args("h264_nvenc")
        
        assert "-hwaccel" in args
        assert "cuda" in args
        assert hw_type == "cuda"
    
    def test_qsv_decode_args(self, capabilities_qsv, hw_config):
        """Should return QSV decode args."""
        selector = EncoderSelector(capabilities_qsv, hw_config)
        args, hw_type = selector.get_hw_decode_args("h264_qsv")
        
        assert "-hwaccel" in args
        assert "qsv" in args
        assert hw_type == "qsv"
    
    def test_software_no_decode_args(self, capabilities_software, hw_config):
        """Should return empty args for software."""
        selector = EncoderSelector(capabilities_software, hw_config)
        args, hw_type = selector.get_hw_decode_args("libx264")
        
        assert args == []
        assert hw_type is None


# =============================================================================
# HW ACCEL DETECTION TESTS
# =============================================================================

class TestHWAccelDetection:
    """Tests for hardware acceleration detection."""
    
    def test_detect_nvenc(self, capabilities_nvenc, hw_config):
        """Should detect NVENC usage."""
        selector = EncoderSelector(capabilities_nvenc, hw_config)
        result = selector.detect_hw_accel_used("h264_nvenc")
        
        assert result == "nvenc"
    
    def test_detect_qsv(self, capabilities_qsv, hw_config):
        """Should detect QSV usage."""
        selector = EncoderSelector(capabilities_qsv, hw_config)
        result = selector.detect_hw_accel_used("h264_qsv")
        
        assert result == "qsv"
    
    def test_detect_software(self, capabilities_software, hw_config):
        """Should detect software encoding."""
        selector = EncoderSelector(capabilities_software, hw_config)
        result = selector.detect_hw_accel_used("libx264")
        
        assert result == "software"


# =============================================================================
# HW ERROR DETECTION TESTS
# =============================================================================

class TestHWErrorDetection:
    """Tests for hardware error detection."""
    
    def test_detect_cuda_error(self, capabilities_nvenc, hw_config):
        """Should detect CUDA errors."""
        selector = EncoderSelector(capabilities_nvenc, hw_config)
        
        assert selector.is_hw_error("CUDA error: no capable devices found")
        assert selector.is_hw_error("cuda initialization failed")
    
    def test_detect_nvenc_error(self, capabilities_nvenc, hw_config):
        """Should detect NVENC errors."""
        selector = EncoderSelector(capabilities_nvenc, hw_config)
        
        assert selector.is_hw_error("nvenc encoder initialization failed")
        assert selector.is_hw_error("Cannot open NVENC codec")
    
    def test_detect_qsv_error(self, capabilities_qsv, hw_config):
        """Should detect QSV errors."""
        selector = EncoderSelector(capabilities_qsv, hw_config)
        
        assert selector.is_hw_error("qsv session initialization failed")
    
    def test_detect_vaapi_error(self, capabilities_software, hw_config):
        """Should detect VAAPI errors."""
        selector = EncoderSelector(capabilities_software, hw_config)
        
        assert selector.is_hw_error("vaapi device not available")
    
    def test_no_false_positive(self, capabilities_nvenc, hw_config):
        """Should not flag non-hardware errors."""
        selector = EncoderSelector(capabilities_nvenc, hw_config)
        
        assert selector.is_hw_error("File not found") is False
        assert selector.is_hw_error("Invalid input") is False


# =============================================================================
# MARK HW FAILED TESTS
# =============================================================================

class TestMarkHWFailed:
    """Tests for marking hardware as failed."""
    
    def test_mark_encoder_failed(self, capabilities_nvenc, hw_config):
        """Should track failed encoder."""
        selector = EncoderSelector(capabilities_nvenc, hw_config)
        
        selector.mark_hw_failed("h264_nvenc")
        
        assert selector.is_encoder_failed("h264_nvenc")
        assert "h264_nvenc" in selector._failed_encoders
    
    def test_mark_updates_capabilities(self, capabilities_nvenc, hw_config):
        """Should update capabilities when encoder fails."""
        selector = EncoderSelector(capabilities_nvenc, hw_config)
        
        # Verify NVENC is initially available
        nvenc_cap = None
        for hw in selector.capabilities.hw_accels:
            if hw.type == HWAccelType.NVENC:
                nvenc_cap = hw
                break
        
        assert nvenc_cap is not None
        assert nvenc_cap.available is True
        
        # Mark as failed
        selector.mark_hw_failed("h264_nvenc")
        
        # Should now be unavailable
        assert nvenc_cap.available is False
    
    def test_is_encoder_failed_false(self, capabilities_nvenc, hw_config):
        """Should return False for non-failed encoder."""
        selector = EncoderSelector(capabilities_nvenc, hw_config)
        
        assert selector.is_encoder_failed("h264_nvenc") is False
    
    def test_reset_failed_encoders(self, capabilities_nvenc, hw_config):
        """Should reset failed encoders list."""
        selector = EncoderSelector(capabilities_nvenc, hw_config)
        
        selector.mark_hw_failed("h264_nvenc")
        selector.mark_hw_failed("hevc_nvenc")
        
        assert len(selector._failed_encoders) == 2
        
        selector.reset_failed_encoders()
        
        assert len(selector._failed_encoders) == 0
    
    def test_encoder_to_hw_type_nvenc(self, capabilities_nvenc, hw_config):
        """Should map NVENC encoders correctly."""
        selector = EncoderSelector(capabilities_nvenc, hw_config)
        
        assert selector._encoder_to_hw_type("h264_nvenc") == HWAccelType.NVENC
        assert selector._encoder_to_hw_type("hevc_nvenc") == HWAccelType.NVENC
    
    def test_encoder_to_hw_type_qsv(self, capabilities_qsv, hw_config):
        """Should map QSV encoders correctly."""
        selector = EncoderSelector(capabilities_qsv, hw_config)
        
        assert selector._encoder_to_hw_type("h264_qsv") == HWAccelType.QSV
        assert selector._encoder_to_hw_type("hevc_qsv") == HWAccelType.QSV
    
    def test_encoder_to_hw_type_software(self, capabilities_software, hw_config):
        """Should return None for software encoders."""
        selector = EncoderSelector(capabilities_software, hw_config)
        
        assert selector._encoder_to_hw_type("libx264") is None
        assert selector._encoder_to_hw_type("libx265") is None


# =============================================================================
# FALLBACK BEHAVIOR TESTS
# =============================================================================

class TestFallbackBehavior:
    """Tests for fallback behavior when hardware fails."""
    
    def test_fallback_after_marking_failed(self, capabilities_nvenc, hw_config):
        """Should fallback to software after marking HW failed."""
        selector = EncoderSelector(capabilities_nvenc, hw_config)
        
        # First selection should be NVENC
        encoder1, _ = selector.get_video_encoder(VideoCodec.H264, HWAccel.AUTO)
        assert "nvenc" in encoder1
        
        # Mark NVENC as failed
        selector.mark_hw_failed("h264_nvenc")
        
        # Next selection should fallback to software
        encoder2, _ = selector.get_video_encoder(VideoCodec.H264, HWAccel.AUTO)
        assert encoder2 == "libx264"

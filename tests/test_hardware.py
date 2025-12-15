"""
Tests for GhostStream hardware detection
"""

import pytest
from unittest.mock import patch, MagicMock

from ghoststream.hardware import (
    HardwareDetector, HWAccelType, Capabilities,
    get_capabilities
)


class TestHardwareDetector:
    """Tests for HardwareDetector class."""
    
    def test_ffmpeg_version_detection(self):
        """Should detect FFmpeg version."""
        detector = HardwareDetector()
        version = detector.get_ffmpeg_version()
        assert version != ""
    
    def test_get_encoders(self):
        """Should get list of encoders."""
        detector = HardwareDetector()
        encoders = detector.get_ffmpeg_encoders()
        
        assert "video" in encoders
        assert "audio" in encoders
        assert isinstance(encoders["video"], list)
        assert isinstance(encoders["audio"], list)
    
    def test_get_decoders(self):
        """Should get list of decoders."""
        detector = HardwareDetector()
        decoders = detector.get_ffmpeg_decoders()
        
        assert "video" in decoders
        assert "audio" in decoders
    
    def test_detect_software_always_available(self):
        """Software encoding should always be available."""
        detector = HardwareDetector()
        capability = detector.detect_software()
        
        assert capability.available is True
        assert capability.type == HWAccelType.SOFTWARE
        assert len(capability.encoders) > 0
    
    def test_detect_all_returns_capabilities(self):
        """detect_all should return Capabilities object."""
        detector = HardwareDetector()
        capabilities = detector.detect_all()
        
        assert isinstance(capabilities, Capabilities)
        assert len(capabilities.hw_accels) > 0
        assert capabilities.platform != ""
        assert capabilities.ffmpeg_version != ""


class TestCapabilities:
    """Tests for Capabilities class."""
    
    def test_get_best_hw_accel_fallback(self):
        """Should fallback to software if no hw accel available."""
        capabilities = Capabilities()
        # No hw_accels set, should return software
        assert capabilities.get_best_hw_accel() == HWAccelType.SOFTWARE
    
    def test_to_dict(self):
        """Should convert to dictionary."""
        capabilities = Capabilities(
            platform="Test",
            ffmpeg_version="1.0",
            video_codecs=["h264"],
            audio_codecs=["aac"]
        )
        
        d = capabilities.to_dict()
        
        assert d["platform"] == "Test"
        assert d["ffmpeg_version"] == "1.0"
        assert "h264" in d["video_codecs"]


class TestGetCapabilities:
    """Tests for get_capabilities function."""
    
    def test_caches_result(self):
        """Should cache capabilities."""
        caps1 = get_capabilities()
        caps2 = get_capabilities()
        
        # Same object (cached)
        assert caps1 is caps2
    
    def test_force_refresh(self):
        """Should refresh when forced."""
        caps1 = get_capabilities()
        caps2 = get_capabilities(force_refresh=True)
        
        # Different objects
        assert caps1 is not caps2

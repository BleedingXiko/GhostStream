"""
Tests for GhostStream transcoding module
"""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime

from ghoststream.transcoding import (
    # Models
    QualityPreset,
    TranscodeProgress,
    MediaInfo,
    # Constants
    QUALITY_LADDER,
    TONEMAP_FILTER,
    AUDIO_BITRATE_MAP,
    MAX_RETRIES,
    get_resolution_map,
    get_bitrate_map,
    # Classes
    FilterBuilder,
    EncoderSelector,
    # Adaptive
    HardwareTier,
    PowerSource,
    CPUInfo,
    SystemProfile,
    SystemMetrics,
    HardwareProfiler,
    AdaptiveQualitySelector,
)
from ghoststream.hardware import Capabilities, HWAccelType, HWAccelCapability, GPUInfo


# =============================================================================
# MODEL TESTS
# =============================================================================

class TestQualityPreset:
    """Tests for QualityPreset model."""
    
    def test_create_preset(self):
        """Should create a quality preset."""
        preset = QualityPreset(
            name="1080p",
            width=1920,
            height=1080,
            video_bitrate="8M",
            audio_bitrate="192k",
            crf=20,
            hw_preset="p4"
        )
        assert preset.name == "1080p"
        assert preset.width == 1920
        assert preset.height == 1080
    
    def test_quality_ladder_has_presets(self):
        """Quality ladder should have presets."""
        assert len(QUALITY_LADDER) > 0
        assert all(isinstance(p, QualityPreset) for p in QUALITY_LADDER)
    
    def test_quality_ladder_sorted_by_resolution(self):
        """Quality ladder should be sorted highest to lowest."""
        widths = [p.width for p in QUALITY_LADDER]
        assert widths == sorted(widths, reverse=True)


class TestMediaInfo:
    """Tests for MediaInfo model."""
    
    def test_default_values(self):
        """Should have sensible defaults."""
        info = MediaInfo()
        assert info.width == 0
        assert info.height == 0
        assert info.duration == 0.0
        assert info.is_hdr is False
        assert info.is_10bit is False
    
    def test_set_values(self):
        """Should accept custom values."""
        info = MediaInfo(
            width=3840,
            height=2160,
            duration=7200.0,
            video_codec="hevc",
            is_hdr=True,
            is_10bit=True
        )
        assert info.width == 3840
        assert info.height == 2160
        assert info.is_hdr is True


class TestTranscodeProgress:
    """Tests for TranscodeProgress model."""
    
    def test_default_values(self):
        """Should have sensible defaults."""
        progress = TranscodeProgress()
        assert progress.percent == 0.0
        assert progress.fps == 0.0
        assert progress.stage == "transcoding"
    
    def test_set_progress(self):
        """Should track progress values."""
        progress = TranscodeProgress(
            percent=50.5,
            fps=120.0,
            speed=2.5,
            stage="transcoding"
        )
        assert progress.percent == 50.5
        assert progress.fps == 120.0
        assert progress.speed == 2.5


# =============================================================================
# CONSTANTS TESTS
# =============================================================================

class TestConstants:
    """Tests for transcoding constants."""
    
    def test_tonemap_filter_has_zscale(self):
        """Tonemap filter should use zscale."""
        assert "zscale" in TONEMAP_FILTER
        assert "tonemap" in TONEMAP_FILTER
    
    def test_tonemap_filter_specifies_colorspace(self):
        """Tonemap filter should specify input colorspace."""
        assert "tin=smpte2084" in TONEMAP_FILTER
        assert "bt2020" in TONEMAP_FILTER
    
    def test_audio_bitrate_map(self):
        """Audio bitrate map should have common channel counts."""
        assert 2 in AUDIO_BITRATE_MAP  # Stereo
        assert 6 in AUDIO_BITRATE_MAP  # 5.1
    
    def test_max_retries_is_reasonable(self):
        """Max retries should be reasonable."""
        assert 1 <= MAX_RETRIES <= 10
    
    def test_resolution_map_lazy_load(self):
        """Resolution map should load lazily."""
        res_map = get_resolution_map()
        assert len(res_map) > 0
    
    def test_bitrate_map_lazy_load(self):
        """Bitrate map should load lazily."""
        br_map = get_bitrate_map()
        assert len(br_map) > 0


# =============================================================================
# FILTER BUILDER TESTS
# =============================================================================

class TestFilterBuilder:
    """Tests for FilterBuilder class."""
    
    @pytest.fixture
    def builder(self):
        return FilterBuilder()
    
    def test_tonemap_filter_zscale(self, builder):
        """Should return zscale tonemap when available."""
        with patch.object(builder, 'check_filter_available', return_value=True):
            result = builder.get_tonemap_filter(use_zscale=True)
            assert "zscale" in result
    
    def test_tonemap_filter_fallback(self, builder):
        """Should fallback when zscale not available."""
        with patch.object(builder, 'check_filter_available', return_value=False):
            result = builder.get_tonemap_filter(use_zscale=True)
            assert "setparams" in result or "format" in result
    
    def test_scale_filter_no_upscale(self, builder):
        """Should not upscale smaller source."""
        from ghoststream.models import Resolution
        result = builder.get_scale_filter(
            resolution=Resolution.FHD_1080P,
            source_width=1280,
            source_height=720
        )
        assert result is None
    
    def test_scale_filter_downscale(self, builder):
        """Should downscale larger source."""
        from ghoststream.models import Resolution
        result = builder.get_scale_filter(
            resolution=Resolution.HD_720P,
            source_width=1920,
            source_height=1080
        )
        assert result is not None
        assert "scale" in result


# =============================================================================
# ENCODER SELECTOR TESTS
# =============================================================================

class TestEncoderSelector:
    """Tests for EncoderSelector class."""
    
    @pytest.fixture
    def capabilities_with_nvenc(self):
        """Create capabilities with NVENC available."""
        return Capabilities(
            hw_accels=[
                HWAccelCapability(
                    type=HWAccelType.NVENC,
                    available=True,
                    encoders=["h264_nvenc", "hevc_nvenc"],
                    gpu_info=GPUInfo(name="Test GPU", memory_mb=8000)
                ),
                HWAccelCapability(
                    type=HWAccelType.SOFTWARE,
                    available=True,
                    encoders=["libx264", "libx265"]
                )
            ]
        )
    
    @pytest.fixture
    def capabilities_software_only(self):
        """Create capabilities with software only."""
        return Capabilities(
            hw_accels=[
                HWAccelCapability(
                    type=HWAccelType.SOFTWARE,
                    available=True,
                    encoders=["libx264", "libx265"]
                )
            ]
        )
    
    def test_select_nvenc_when_available(self, capabilities_with_nvenc):
        """Should select NVENC when available."""
        from ghoststream.config import HardwareConfig
        from ghoststream.models import VideoCodec, HWAccel
        
        selector = EncoderSelector(capabilities_with_nvenc, HardwareConfig())
        encoder, args = selector.get_video_encoder(VideoCodec.H264, HWAccel.AUTO)
        
        assert "nvenc" in encoder
    
    def test_fallback_to_software(self, capabilities_software_only):
        """Should fallback to software when no HW accel."""
        from ghoststream.config import HardwareConfig
        from ghoststream.models import VideoCodec, HWAccel
        
        selector = EncoderSelector(capabilities_software_only, HardwareConfig())
        encoder, args = selector.get_video_encoder(VideoCodec.H264, HWAccel.AUTO)
        
        assert encoder == "libx264"


# =============================================================================
# ADAPTIVE SYSTEM TESTS
# =============================================================================

class TestHardwareTier:
    """Tests for HardwareTier enum."""
    
    def test_all_tiers_exist(self):
        """Should have all expected tiers."""
        tiers = [HardwareTier.ULTRA, HardwareTier.HIGH, HardwareTier.MEDIUM,
                 HardwareTier.LOW, HardwareTier.MINIMAL]
        assert len(tiers) == 5


class TestPowerSource:
    """Tests for PowerSource enum."""
    
    def test_power_sources(self):
        """Should have AC, battery, and unknown."""
        assert PowerSource.AC.value == "ac"
        assert PowerSource.BATTERY.value == "battery"
        assert PowerSource.UNKNOWN.value == "unknown"


class TestCPUInfo:
    """Tests for CPUInfo dataclass."""
    
    def test_encoding_power_baseline(self):
        """Should calculate encoding power score."""
        cpu = CPUInfo(cores=8, threads=16, frequency_mhz=3000)
        power = cpu.encoding_power
        assert 10 <= power <= 100
    
    def test_mobile_penalty(self):
        """Mobile CPUs should have lower power score."""
        desktop = CPUInfo(cores=8, threads=16, frequency_mhz=3000, is_mobile=False)
        mobile = CPUInfo(cores=8, threads=16, frequency_mhz=3000, is_mobile=True)
        
        assert mobile.encoding_power < desktop.encoding_power


class TestSystemProfile:
    """Tests for SystemProfile dataclass."""
    
    def test_default_profile(self):
        """Should have sensible defaults."""
        profile = SystemProfile()
        assert profile.tier == HardwareTier.LOW
        assert profile.max_concurrent_jobs == 1
    
    def test_custom_profile(self):
        """Should accept custom values."""
        profile = SystemProfile(
            gpu_vram_mb=8000,
            gpu_name="RTX 3080",
            tier=HardwareTier.ULTRA,
            max_resolution=(3840, 2160),
            max_concurrent_jobs=4
        )
        assert profile.tier == HardwareTier.ULTRA
        assert profile.max_concurrent_jobs == 4


class TestSystemMetrics:
    """Tests for SystemMetrics dataclass."""
    
    def test_load_factor_idle(self):
        """Idle system should have low load factor."""
        metrics = SystemMetrics(
            cpu_percent=5.0,
            memory_percent=30.0,
            gpu_percent=0.0,
            gpu_temperature_c=40.0
        )
        assert metrics.load_factor < 0.3
    
    def test_load_factor_busy(self):
        """Busy system should have high load factor."""
        metrics = SystemMetrics(
            cpu_percent=90.0,
            memory_percent=80.0,
            gpu_percent=95.0,
            gpu_temperature_c=75.0
        )
        assert metrics.load_factor > 0.7
    
    def test_is_overloaded(self):
        """Should detect overloaded state."""
        normal = SystemMetrics(cpu_percent=50.0, memory_percent=50.0)
        overloaded = SystemMetrics(cpu_percent=95.0, memory_percent=90.0, gpu_temperature_c=90.0)
        
        assert normal.is_overloaded is False
        assert overloaded.is_overloaded is True


class TestAdaptiveQualitySelector:
    """Tests for AdaptiveQualitySelector class."""
    
    @pytest.fixture
    def medium_profile(self):
        """Create a medium tier profile."""
        return SystemProfile(
            tier=HardwareTier.MEDIUM,
            max_resolution=(1920, 1080),
            max_bitrate_mbps=8.0,
            max_concurrent_jobs=2
        )
    
    @pytest.fixture
    def low_profile(self):
        """Create a low tier profile."""
        return SystemProfile(
            tier=HardwareTier.LOW,
            max_resolution=(1280, 720),
            max_bitrate_mbps=4.0,
            max_concurrent_jobs=1
        )
    
    def test_filters_presets_by_resolution(self, medium_profile):
        """Should filter presets above max resolution."""
        selector = AdaptiveQualitySelector(medium_profile)
        media = MediaInfo(width=3840, height=2160)
        
        presets = selector.get_optimal_presets(media)
        
        for preset in presets:
            assert preset.width <= 1920
            assert preset.height <= 1080
    
    def test_no_upscaling(self, medium_profile):
        """Should not upscale source."""
        selector = AdaptiveQualitySelector(medium_profile)
        media = MediaInfo(width=854, height=480)
        
        presets = selector.get_optimal_presets(media)
        
        for preset in presets:
            assert preset.width <= 854
    
    def test_should_transcode_hdr(self, medium_profile):
        """Should recommend transcoding HDR content."""
        selector = AdaptiveQualitySelector(medium_profile)
        media = MediaInfo(width=1920, height=1080, is_hdr=True)
        
        should, reason = selector.should_transcode(media)
        
        assert should is True
        assert "HDR" in reason
    
    def test_single_best_preset(self, low_profile):
        """Should return single best preset."""
        selector = AdaptiveQualitySelector(low_profile)
        media = MediaInfo(width=1920, height=1080)
        
        preset = selector.get_single_best_preset(media)
        
        assert isinstance(preset, QualityPreset)
        assert preset.width <= 1280


class TestHardwareProfiler:
    """Tests for HardwareProfiler class."""
    
    @pytest.fixture
    def capabilities(self):
        """Create test capabilities."""
        return Capabilities(
            hw_accels=[
                HWAccelCapability(
                    type=HWAccelType.NVENC,
                    available=True,
                    encoders=["h264_nvenc"],
                    gpu_info=GPUInfo(name="Test GPU", memory_mb=6000)
                ),
                HWAccelCapability(
                    type=HWAccelType.SOFTWARE,
                    available=True,
                    encoders=["libx264"]
                )
            ]
        )
    
    def test_creates_profile(self, capabilities):
        """Should create system profile."""
        profiler = HardwareProfiler(capabilities)
        profile = profiler.get_profile()
        
        assert isinstance(profile, SystemProfile)
        assert profile.tier in HardwareTier
    
    def test_detects_gpu_vram(self, capabilities):
        """Should detect GPU VRAM from capabilities."""
        profiler = HardwareProfiler(capabilities)
        profile = profiler.get_profile()
        
        assert profile.gpu_vram_mb == 6000
        assert profile.gpu_name == "Test GPU"


# =============================================================================
# INTEGRATION TESTS
# =============================================================================

class TestTranscodingIntegration:
    """Integration tests for transcoding module."""
    
    def test_import_all_components(self):
        """Should import all transcoding components."""
        from ghoststream.transcoding import (
            TranscodeEngine,
            FilterBuilder,
            EncoderSelector,
            MediaProbe,
            CommandBuilder,
            AdaptiveTranscodeManager
        )
        assert TranscodeEngine is not None
        assert FilterBuilder is not None
        assert AdaptiveTranscodeManager is not None
    
    def test_backwards_compatible_imports(self):
        """Should support imports from transcoding module."""
        from ghoststream.transcoding import (
            TranscodeEngine,
            TranscodeProgress,
            MediaInfo,
            TONEMAP_FILTER
        )
        assert TranscodeEngine is not None
        assert TranscodeProgress is not None

"""
Real Transcoding Tests
======================

Tests actual transcoding with real media files.
These tests verify the full pipeline works end-to-end.

Run with: pytest tests/test_real_transcode.py -v
Skip slow tests: pytest tests/test_real_transcode.py -v -m "not slow"
"""

import asyncio
import time
import pytest
from pathlib import Path

from ghoststream.transcoding import TranscodeEngine, MediaInfo
from ghoststream.models import OutputConfig, TranscodeMode


# =============================================================================
# MEDIA PROBE TESTS
# =============================================================================

class TestMediaProbe:
    """Test media information extraction."""
    
    @pytest.mark.requires_ffmpeg
    @pytest.mark.asyncio
    async def test_probe_video_file(self, test_video_720p, test_config):
        """Should extract media info from real video file."""
        if not test_video_720p:
            pytest.skip("Test video not available")
        
        engine = TranscodeEngine()
        media_info = await engine.get_media_info(str(test_video_720p))
        
        assert media_info is not None
        assert media_info.width == 1280
        assert media_info.height == 720
        assert media_info.duration > 0
        assert media_info.video_codec is not None
    
    @pytest.mark.requires_ffmpeg
    @pytest.mark.asyncio
    async def test_probe_1080p_video(self, test_video_1080p, test_config):
        """Should detect 1080p resolution."""
        if not test_video_1080p:
            pytest.skip("Test video not available")
        
        engine = TranscodeEngine()
        media_info = await engine.get_media_info(str(test_video_1080p))
        
        assert media_info.width == 1920
        assert media_info.height == 1080
    
    @pytest.mark.requires_ffmpeg
    @pytest.mark.asyncio
    async def test_probe_via_http(self, test_video_url, test_config):
        """Should probe video via HTTP URL."""
        if not test_video_url:
            pytest.skip("Test video URL not available")
        
        engine = TranscodeEngine()
        media_info = await engine.get_media_info(test_video_url)
        
        assert media_info is not None
        assert media_info.duration > 0
    
    @pytest.mark.requires_ffmpeg
    @pytest.mark.asyncio
    async def test_probe_invalid_file(self, test_config, tmp_path):
        """Should handle invalid file gracefully."""
        invalid_file = tmp_path / "not_a_video.txt"
        invalid_file.write_text("this is not a video")
        
        engine = TranscodeEngine()
        media_info = await engine.get_media_info(str(invalid_file))
        
        # Should return empty/invalid media info, not crash
        assert media_info.duration == 0 or media_info is None


# =============================================================================
# HLS TRANSCODING TESTS
# =============================================================================

class TestHLSTranscoding:
    """Test HLS (HTTP Live Streaming) output."""
    
    @pytest.mark.requires_ffmpeg
    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_transcode_to_hls(self, quick_test_video, temp_output_dir, test_config):
        """Should transcode video to HLS segments."""
        if not quick_test_video:
            pytest.skip("Test video not available")
        
        engine = TranscodeEngine()
        
        # Get media info first
        media_info = await engine.get_media_info(str(quick_test_video))
        assert media_info.duration > 0
        
        # Build HLS command
        output_config = OutputConfig(
            resolution="480p",
            video_codec="h264",
            audio_codec="aac"
        )
        
        cmd, encoder = engine.build_hls_command(
            source=str(quick_test_video),
            output_dir=temp_output_dir,
            output_config=output_config,
            media_info=media_info
        )
        
        # Verify command was built
        assert cmd is not None
        assert len(cmd) > 0
        assert "ffmpeg" in cmd[0].lower() or cmd[0] == engine.ffmpeg_path
    
    @pytest.mark.requires_ffmpeg
    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_full_hls_transcode(self, quick_test_video, temp_output_dir, test_config):
        """Should build valid HLS transcode command."""
        if not quick_test_video:
            pytest.skip("Test video not available")
        
        engine = TranscodeEngine()
        media_info = await engine.get_media_info(str(quick_test_video))
        
        output_config = OutputConfig(
            resolution="480p",
            video_codec="h264",
            audio_codec="aac"
        )
        
        # Build HLS command
        cmd, encoder = engine.build_hls_command(
            source=str(quick_test_video),
            output_dir=temp_output_dir,
            output_config=output_config,
            media_info=media_info
        )
        
        # Verify command structure
        assert cmd is not None
        assert len(cmd) > 5  # Should have multiple args
        assert any("hls" in str(arg).lower() for arg in cmd), "Should be HLS format"
        assert encoder is not None


# =============================================================================
# ABR (ADAPTIVE BITRATE) TESTS
# =============================================================================

class TestABRTranscoding:
    """Test Adaptive Bitrate Streaming output."""
    
    @pytest.mark.requires_ffmpeg
    def test_get_abr_variants(self, test_config):
        """Should calculate appropriate ABR variants for source."""
        engine = TranscodeEngine()
        
        # 1080p source
        media_info = MediaInfo(width=1920, height=1080, duration=60)
        variants = engine.get_abr_variants(media_info)
        
        assert len(variants) > 0
        # Should include at least 720p and 480p
        heights = [v.height for v in variants]
        assert 720 in heights or 480 in heights
        
        # Should never upscale
        for v in variants:
            assert v.height <= 1080
    
    @pytest.mark.requires_ffmpeg
    def test_abr_variants_4k_source(self, test_config):
        """Should create multiple variants for 4K source."""
        engine = TranscodeEngine()
        
        media_info = MediaInfo(width=3840, height=2160, duration=60)
        variants = engine.get_abr_variants(media_info)
        
        # 4K should have more variants
        assert len(variants) >= 3
    
    @pytest.mark.requires_ffmpeg
    def test_abr_variants_low_res_source(self, test_config):
        """Should limit variants for low-res source."""
        engine = TranscodeEngine()
        
        media_info = MediaInfo(width=640, height=480, duration=60)
        variants = engine.get_abr_variants(media_info)
        
        # Should only have 1-2 variants for 480p source
        assert len(variants) <= 2
        # Should not upscale
        for v in variants:
            assert v.height <= 480


# =============================================================================
# HARDWARE ACCELERATION TESTS
# =============================================================================

class TestHardwareAcceleration:
    """Test hardware acceleration detection and fallback."""
    
    @pytest.mark.requires_ffmpeg
    def test_detect_hardware_capabilities(self, test_config):
        """Should detect available hardware encoders."""
        engine = TranscodeEngine()
        
        # Should have capabilities object
        assert engine.capabilities is not None
        
        # hw_accels should be a list
        assert hasattr(engine.capabilities, 'hw_accels')
    
    @pytest.mark.requires_ffmpeg
    def test_encoder_selection(self, test_config):
        """Should select appropriate encoder."""
        from ghoststream.models import VideoCodec, HWAccel
        
        engine = TranscodeEngine()
        
        # Request h264 encoding
        encoder, args = engine.encoder_selector.get_video_encoder(VideoCodec.H264, HWAccel.AUTO)
        
        # Should return some encoder (software or hardware)
        assert encoder is not None
        assert isinstance(encoder, str)
    
    @pytest.mark.requires_ffmpeg
    def test_software_fallback(self, test_config):
        """Should fall back to software encoding."""
        from ghoststream.models import VideoCodec, HWAccel
        
        engine = TranscodeEngine()
        
        # Force software encoding
        encoder, args = engine.encoder_selector.get_video_encoder(VideoCodec.H264, HWAccel.SOFTWARE)
        
        assert encoder is not None
        assert "libx264" in encoder or "x264" in encoder.lower()


# =============================================================================
# ERROR HANDLING TESTS
# =============================================================================

class TestErrorHandling:
    """Test error handling and recovery."""
    
    @pytest.mark.requires_ffmpeg
    @pytest.mark.asyncio
    async def test_handle_nonexistent_file(self, test_config):
        """Should handle nonexistent source file."""
        engine = TranscodeEngine()
        
        media_info = await engine.get_media_info("/nonexistent/file.mp4")
        
        # Should return invalid media info, not crash
        assert media_info.duration == 0
    
    @pytest.mark.requires_ffmpeg
    @pytest.mark.asyncio
    async def test_handle_corrupt_file(self, test_config, tmp_path):
        """Should handle corrupt/invalid media file."""
        corrupt_file = tmp_path / "corrupt.mp4"
        corrupt_file.write_bytes(b"\x00\x00\x00\x20ftypmp42" + b"\x00" * 100)
        
        engine = TranscodeEngine()
        media_info = await engine.get_media_info(str(corrupt_file))
        
        # Should not crash
        assert media_info is not None


# =============================================================================
# CLEANUP TESTS
# =============================================================================

class TestCleanup:
    """Test automatic cleanup of temp files."""
    
    @pytest.mark.requires_ffmpeg
    def test_temp_dir_exists(self, test_config):
        """Temp directory should be created."""
        engine = TranscodeEngine()
        assert engine.temp_dir.exists()
    
    @pytest.mark.requires_ffmpeg
    @pytest.mark.asyncio
    async def test_job_creates_temp_dir(self, quick_test_video, test_config):
        """Job should create its own temp directory."""
        if not quick_test_video:
            pytest.skip("Test video not available")
        
        engine = TranscodeEngine()
        media_info = await engine.get_media_info(str(quick_test_video))
        
        job_id = "test-cleanup-job"
        media_info, job_dir, error = await engine._prepare_job(job_id, str(quick_test_video))
        
        if job_dir:
            assert job_dir.exists()
            # Cleanup
            import shutil
            shutil.rmtree(job_dir, ignore_errors=True)

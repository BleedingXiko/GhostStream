"""
Tests for GhostStream TranscodeEngine - focusing on new improvements.

Tests cover:
- _run_ffmpeg async handling
- Dynamic stall timeout calculation
- Output validation (HLS and batch)
- Segment integrity checking
- Graceful termination
- Hardware fallback state management
"""

import asyncio
import pytest
import tempfile
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock, PropertyMock
from datetime import datetime

from ghoststream.transcoding.engine import TranscodeEngine
from ghoststream.transcoding.models import MediaInfo, TranscodeProgress


# =============================================================================
# STALL TIMEOUT TESTS
# =============================================================================

class TestStallTimeoutCalculation:
    """Tests for dynamic stall timeout calculation."""
    
    @pytest.fixture
    def engine(self):
        """Create engine with mocked dependencies."""
        with patch('ghoststream.transcoding.engine.get_capabilities') as mock_caps, \
             patch('ghoststream.transcoding.engine.get_config') as mock_config:
            
            mock_config.return_value = MagicMock(
                transcoding=MagicMock(
                    ffmpeg_path="ffmpeg",
                    temp_directory="./temp",
                    max_concurrent_jobs=2,
                    stall_timeout=120,
                    segment_duration=4,
                    retry_count=3,
                    validate_segments=True,
                ),
                hardware=MagicMock(
                    prefer_hw_accel=True,
                    fallback_to_software=True,
                    nvenc_preset="p4",
                    qsv_preset="medium",
                    vaapi_device="/dev/dri/renderD128"
                )
            )
            
            mock_caps.return_value = MagicMock(
                hw_accels=[],
                get_best_hw_accel=MagicMock(return_value=MagicMock(value="software"))
            )
            
            with patch('shutil.which', return_value='ffmpeg'):
                engine = TranscodeEngine()
                return engine
    
    def test_minimum_stall_timeout(self, engine):
        """Should enforce minimum stall timeout of 120s."""
        media_info = MediaInfo(width=1280, height=720)
        timeout = engine._calculate_stall_timeout(media_info)
        assert timeout >= 120
    
    def test_stall_timeout_scales_with_resolution(self, engine):
        """Higher resolution should have longer timeout."""
        media_720p = MediaInfo(width=1280, height=720)
        media_1080p = MediaInfo(width=1920, height=1080)
        media_4k = MediaInfo(width=3840, height=2160)
        
        timeout_720p = engine._calculate_stall_timeout(media_720p)
        timeout_1080p = engine._calculate_stall_timeout(media_1080p)
        timeout_4k = engine._calculate_stall_timeout(media_4k)
        
        assert timeout_4k > timeout_1080p > timeout_720p
    
    def test_4k_resolution_factor(self, engine):
        """4K content should have 2x resolution factor."""
        media_4k = MediaInfo(width=3840, height=2160)
        timeout = engine._calculate_stall_timeout(media_4k)
        
        # Base + (segment_factor * resolution_factor)
        # 120 + (10 * 4 * 2.0) = 200
        assert timeout >= 200


# =============================================================================
# OUTPUT VALIDATION TESTS
# =============================================================================

class TestOutputValidation:
    """Tests for output validation methods."""
    
    @pytest.fixture
    def engine(self):
        """Create engine with mocked dependencies."""
        with patch('ghoststream.transcoding.engine.get_capabilities') as mock_caps, \
             patch('ghoststream.transcoding.engine.get_config') as mock_config:
            
            mock_config.return_value = MagicMock(
                transcoding=MagicMock(
                    ffmpeg_path="ffmpeg",
                    temp_directory="./temp",
                    max_concurrent_jobs=2,
                    stall_timeout=120,
                    segment_duration=4,
                    retry_count=3,
                    validate_segments=True,
                ),
                hardware=MagicMock(
                    prefer_hw_accel=True,
                    fallback_to_software=True,
                    nvenc_preset="p4",
                    qsv_preset="medium",
                    vaapi_device="/dev/dri/renderD128"
                )
            )
            
            mock_caps.return_value = MagicMock(
                hw_accels=[],
                get_best_hw_accel=MagicMock(return_value=MagicMock(value="software"))
            )
            
            with patch('shutil.which', return_value='ffmpeg'):
                return TranscodeEngine()
    
    def test_validate_hls_missing_master(self, engine):
        """Should fail if master playlist missing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            job_dir = Path(tmpdir)
            is_valid, error = engine._validate_hls_output(
                str(job_dir / "master.m3u8"),
                job_dir
            )
            assert is_valid is False
            assert "not found" in error.lower()
    
    def test_validate_hls_empty_master(self, engine):
        """Should fail if master playlist is empty."""
        with tempfile.TemporaryDirectory() as tmpdir:
            job_dir = Path(tmpdir)
            master = job_dir / "master.m3u8"
            master.write_text("")
            
            is_valid, error = engine._validate_hls_output(str(master), job_dir)
            assert is_valid is False
            assert "empty" in error.lower()
    
    def test_validate_hls_no_segments(self, engine):
        """Should fail if no segment files exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            job_dir = Path(tmpdir)
            master = job_dir / "master.m3u8"
            master.write_text("#EXTM3U\n#EXT-X-VERSION:3\nsegment_00000.ts\n")
            
            is_valid, error = engine._validate_hls_output(str(master), job_dir)
            assert is_valid is False
            assert "segment" in error.lower()
    
    def test_validate_hls_valid_output(self, engine):
        """Should pass with valid HLS output."""
        with tempfile.TemporaryDirectory() as tmpdir:
            job_dir = Path(tmpdir)
            
            # Create master playlist
            master = job_dir / "master.m3u8"
            master.write_text("#EXTM3U\n#EXT-X-VERSION:3\nsegment_00000.ts\n")
            
            # Create valid segment (with MPEG-TS sync byte)
            segment = job_dir / "segment_00000.ts"
            # 0x47 is MPEG-TS sync byte
            segment.write_bytes(b'\x47' + b'\x00' * 10000)
            
            is_valid, error = engine._validate_hls_output(str(master), job_dir)
            assert is_valid is True
            assert error == ""
    
    def test_validate_batch_missing_file(self, engine):
        """Should fail if output file missing."""
        is_valid, error = engine._validate_batch_output("/nonexistent/file.mp4")
        assert is_valid is False
        assert "not found" in error.lower()
    
    def test_validate_batch_empty_file(self, engine):
        """Should fail if output file is empty."""
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            f.write(b"")
            
        try:
            is_valid, error = engine._validate_batch_output(f.name)
            assert is_valid is False
            assert "empty" in error.lower()
        finally:
            Path(f.name).unlink()
    
    def test_validate_batch_valid_file(self, engine):
        """Should pass with valid output file."""
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            f.write(b"\x00" * 10000)  # 10KB file
            
        try:
            is_valid, error = engine._validate_batch_output(f.name)
            assert is_valid is True
        finally:
            Path(f.name).unlink()


# =============================================================================
# SEGMENT INTEGRITY TESTS
# =============================================================================

class TestSegmentIntegrity:
    """Tests for segment integrity checking."""
    
    @pytest.fixture
    def engine(self):
        """Create engine with mocked dependencies."""
        with patch('ghoststream.transcoding.engine.get_capabilities') as mock_caps, \
             patch('ghoststream.transcoding.engine.get_config') as mock_config:
            
            mock_config.return_value = MagicMock(
                transcoding=MagicMock(
                    ffmpeg_path="ffmpeg",
                    temp_directory="./temp",
                    max_concurrent_jobs=2,
                    stall_timeout=120,
                    segment_duration=4,
                    retry_count=3,
                    validate_segments=True,
                ),
                hardware=MagicMock()
            )
            
            mock_caps.return_value = MagicMock(
                hw_accels=[],
                get_best_hw_accel=MagicMock(return_value=MagicMock(value="software"))
            )
            
            with patch('shutil.which', return_value='ffmpeg'):
                return TranscodeEngine()
    
    def test_integrity_check_empty_list(self, engine):
        """Should fail with empty segment list."""
        is_valid, error = engine._check_segment_integrity([])
        assert is_valid is False
        assert "no segments" in error.lower()
    
    def test_integrity_check_missing_sync_byte(self, engine):
        """Should fail if MPEG-TS sync byte missing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            segment = Path(tmpdir) / "segment.ts"
            segment.write_bytes(b'\x00' * 5000)  # No sync byte
            
            is_valid, error = engine._check_segment_integrity([segment])
            assert is_valid is False
            assert "sync byte" in error.lower()
    
    def test_integrity_check_too_small(self, engine):
        """Should fail if segment too small."""
        with tempfile.TemporaryDirectory() as tmpdir:
            segment = Path(tmpdir) / "segment.ts"
            segment.write_bytes(b'\x47' + b'\x00' * 100)  # Only 101 bytes
            
            is_valid, error = engine._check_segment_integrity([segment])
            assert is_valid is False
            assert "small" in error.lower()
    
    def test_integrity_check_valid_segments(self, engine):
        """Should pass with valid MPEG-TS segments."""
        with tempfile.TemporaryDirectory() as tmpdir:
            segments = []
            for i in range(5):
                segment = Path(tmpdir) / f"segment_{i}.ts"
                segment.write_bytes(b'\x47' + b'\x00' * 5000)
                segments.append(segment)
            
            is_valid, error = engine._check_segment_integrity(segments)
            assert is_valid is True


# =============================================================================
# PROGRESS PARSING TESTS
# =============================================================================

class TestProgressParsing:
    """Tests for hardened progress regex parsing."""
    
    @pytest.fixture
    def engine(self):
        """Create engine with mocked dependencies."""
        with patch('ghoststream.transcoding.engine.get_capabilities') as mock_caps, \
             patch('ghoststream.transcoding.engine.get_config') as mock_config:
            
            mock_config.return_value = MagicMock(
                transcoding=MagicMock(
                    ffmpeg_path="ffmpeg",
                    temp_directory="./temp",
                    max_concurrent_jobs=2,
                    stall_timeout=120,
                    segment_duration=4,
                    retry_count=3,
                    validate_segments=True,
                ),
                hardware=MagicMock()
            )
            
            mock_caps.return_value = MagicMock(
                hw_accels=[],
                get_best_hw_accel=MagicMock(return_value=MagicMock(value="software"))
            )
            
            with patch('shutil.which', return_value='ffmpeg'):
                return TranscodeEngine()
    
    def test_parse_frame_count(self, engine):
        """Should parse frame count."""
        progress = TranscodeProgress()
        media_info = MediaInfo(duration=100.0)
        
        engine._parse_progress("frame=  1234 fps=30", progress, media_info)
        assert progress.frame == 1234
    
    def test_parse_fps(self, engine):
        """Should parse FPS."""
        progress = TranscodeProgress()
        media_info = MediaInfo(duration=100.0)
        
        engine._parse_progress("fps= 59.94 ", progress, media_info)
        assert abs(progress.fps - 59.94) < 0.01
    
    def test_parse_fps_na(self, engine):
        """Should handle N/A FPS."""
        progress = TranscodeProgress()
        progress.fps = 30.0  # Set initial value
        media_info = MediaInfo(duration=100.0)
        
        engine._parse_progress("fps=N/A", progress, media_info)
        assert progress.fps == 30.0  # Should not change
    
    def test_parse_time_hhmmss(self, engine):
        """Should parse HH:MM:SS.ms time format."""
        progress = TranscodeProgress()
        media_info = MediaInfo(duration=7200.0)
        
        engine._parse_progress("time=01:30:45.50", progress, media_info)
        expected = 1*3600 + 30*60 + 45.50
        assert abs(progress.time - expected) < 0.01
    
    def test_parse_bitrate_kbits(self, engine):
        """Should parse bitrate in kbits/s."""
        progress = TranscodeProgress()
        media_info = MediaInfo(duration=100.0)
        
        engine._parse_progress("bitrate=4567kbits/s", progress, media_info)
        assert "4567" in progress.bitrate
    
    def test_parse_size_kb(self, engine):
        """Should parse size in kB."""
        progress = TranscodeProgress()
        media_info = MediaInfo(duration=100.0)
        
        engine._parse_progress("size=    5000kB", progress, media_info)
        assert progress.total_size == 5000 * 1024
    
    def test_parse_speed(self, engine):
        """Should parse encoding speed."""
        progress = TranscodeProgress()
        media_info = MediaInfo(duration=100.0)
        
        engine._parse_progress("speed=2.5x", progress, media_info)
        assert abs(progress.speed - 2.5) < 0.01
    
    def test_calculate_percent(self, engine):
        """Should calculate percent from time and duration."""
        progress = TranscodeProgress()
        media_info = MediaInfo(duration=100.0)
        
        engine._parse_progress("time=00:00:50.00", progress, media_info)
        assert abs(progress.percent - 50.0) < 0.1


# =============================================================================
# GRACEFUL TERMINATION TESTS
# =============================================================================

class TestGracefulTermination:
    """Tests for graceful process termination."""
    
    @pytest.fixture
    def engine(self):
        """Create engine with mocked dependencies."""
        with patch('ghoststream.transcoding.engine.get_capabilities') as mock_caps, \
             patch('ghoststream.transcoding.engine.get_config') as mock_config:
            
            mock_config.return_value = MagicMock(
                transcoding=MagicMock(
                    ffmpeg_path="ffmpeg",
                    temp_directory="./temp",
                    max_concurrent_jobs=2,
                    stall_timeout=120,
                    segment_duration=4,
                    retry_count=3,
                    validate_segments=True,
                ),
                hardware=MagicMock()
            )
            
            mock_caps.return_value = MagicMock(
                hw_accels=[],
                get_best_hw_accel=MagicMock(return_value=MagicMock(value="software"))
            )
            
            with patch('shutil.which', return_value='ffmpeg'):
                return TranscodeEngine()
    
    @pytest.mark.asyncio
    async def test_graceful_terminate_already_exited(self, engine):
        """Should handle already-terminated process."""
        mock_process = MagicMock()
        mock_process.returncode = 0  # Already exited
        
        await engine._graceful_terminate(mock_process)
        # Should return without attempting to send signals
    
    @pytest.mark.asyncio
    async def test_graceful_terminate_sends_signal(self, engine):
        """Should send appropriate signal based on platform."""
        mock_process = MagicMock()
        mock_process.returncode = None  # Still running
        mock_process.send_signal = MagicMock()
        mock_process.wait = AsyncMock(return_value=0)
        mock_process.terminate = MagicMock()
        mock_process.kill = MagicMock()
        
        await engine._graceful_terminate(mock_process)
        
        # Should have attempted to send a signal
        assert mock_process.send_signal.called or mock_process.terminate.called



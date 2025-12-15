"""
Tests for GhostStream configuration
"""

import pytest
import tempfile
import os
from pathlib import Path

from ghoststream.config import (
    GhostStreamConfig, ServerConfig, MDNSConfig,
    TranscodingConfig, load_config, get_config, set_config
)


class TestConfigModels:
    """Tests for configuration models."""
    
    def test_server_config_defaults(self):
        """ServerConfig should have correct defaults."""
        config = ServerConfig()
        assert config.host == "0.0.0.0"
        assert config.port == 8765
    
    def test_mdns_config_defaults(self):
        """MDNSConfig should have correct defaults."""
        config = MDNSConfig()
        assert config.enabled is True
        assert config.service_name == "GhostStream Transcoder"
    
    def test_transcoding_config_defaults(self):
        """TranscodingConfig should have correct defaults."""
        config = TranscodingConfig()
        assert config.ffmpeg_path == "auto"
        assert config.max_concurrent_jobs == 2
        assert config.segment_duration == 4
    
    def test_full_config_defaults(self):
        """GhostStreamConfig should have all defaults."""
        config = GhostStreamConfig()
        assert config.server.port == 8765
        assert config.mdns.enabled is True
        assert config.transcoding.max_concurrent_jobs == 2


class TestLoadConfig:
    """Tests for load_config function."""
    
    def test_load_default_config(self):
        """Should load default config when no file exists."""
        config = load_config("/nonexistent/path.yaml")
        assert config.server.port == 8765
    
    def test_load_yaml_config(self):
        """Should load config from YAML file."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write("""
server:
  host: 127.0.0.1
  port: 9000
mdns:
  enabled: false
transcoding:
  max_concurrent_jobs: 4
""")
            f.flush()
            
            try:
                config = load_config(f.name)
                assert config.server.host == "127.0.0.1"
                assert config.server.port == 9000
                assert config.mdns.enabled is False
                assert config.transcoding.max_concurrent_jobs == 4
            finally:
                os.unlink(f.name)
    
    def test_partial_yaml_config(self):
        """Should merge partial config with defaults."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write("""
server:
  port: 8000
""")
            f.flush()
            
            try:
                config = load_config(f.name)
                # Custom value
                assert config.server.port == 8000
                # Default value
                assert config.server.host == "0.0.0.0"
                assert config.mdns.enabled is True
            finally:
                os.unlink(f.name)


class TestGlobalConfig:
    """Tests for global config accessors."""
    
    def test_get_config_returns_default(self):
        """get_config should return config."""
        config = get_config()
        assert isinstance(config, GhostStreamConfig)
    
    def test_set_config(self):
        """set_config should update global config."""
        custom_config = GhostStreamConfig(
            server=ServerConfig(port=9999)
        )
        set_config(custom_config)
        
        config = get_config()
        assert config.server.port == 9999
        
        # Reset
        set_config(GhostStreamConfig())

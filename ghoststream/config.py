"""
Configuration management for GhostStream
"""

import os
import yaml
from pathlib import Path
from typing import Optional, List
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8765


class MDNSConfig(BaseModel):
    enabled: bool = True
    service_name: str = "GhostStream Transcoder"


class GhostHubConfig(BaseModel):
    url: Optional[str] = None  # e.g., "http://192.168.4.1:5000"
    auto_register: bool = True
    register_interval_seconds: int = 300  # Re-register every 5 minutes


class TranscodingConfig(BaseModel):
    """Transcoding configuration options."""
    ffmpeg_path: str = Field(default_factory=lambda: os.getenv("GHOSTSTREAM_FFMPEG_PATH", "auto"))
    temp_directory: str = "./transcode_temp"
    max_concurrent_jobs: int = 2
    segment_duration: int = 4  # HLS segment duration in seconds
    cleanup_after_hours: int = 24
    
    # Codec defaults
    default_video_codec: str = "h264"
    default_audio_codec: str = "aac"
    
    # ABR (Adaptive Bitrate) options
    enable_abr: bool = True  # Enable adaptive bitrate streaming
    abr_max_variants: int = 4  # Max quality variants for ABR
    
    # Timeout and retry configuration
    stall_timeout: int = 120  # Base seconds before considering FFmpeg stalled (min 120)
    stall_timeout_per_segment: int = 10  # Additional timeout per segment duration second
    retry_count: int = 3  # Number of retries on transient failures
    retry_delay: int = 2  # Base delay between retries in seconds (increases with attempts)
    
    # Processing options
    tone_map_hdr: bool = True  # Auto-convert HDR to SDR
    validate_segments: bool = True  # Verify segment integrity before reporting success
    
    # Scheduling options
    enable_preemption: bool = False  # Allow high-priority jobs to preempt lower priority
    max_queue_size: int = 1000  # Maximum jobs in queue


class HardwareConfig(BaseModel):
    prefer_hw_accel: bool = True
    fallback_to_software: bool = True
    nvenc_preset: str = "p4"
    qsv_preset: str = "medium"
    videotoolbox_preset: str = "medium"
    vaapi_device: str = "/dev/dri/renderD128"


class LimitsConfig(BaseModel):
    max_resolution: str = "4k"
    max_bitrate: str = "50M"
    max_file_size_gb: int = 50


class SecurityConfig(BaseModel):
    api_key: Optional[str] = None
    allowed_origins: List[str] = Field(default_factory=list)
    rate_limit_per_minute: int = 60


class LoggingConfig(BaseModel):
    level: str = "INFO"
    format: str = "json"
    file: Optional[str] = None


class GhostStreamConfig(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    mdns: MDNSConfig = Field(default_factory=MDNSConfig)
    ghosthub: GhostHubConfig = Field(default_factory=GhostHubConfig)
    transcoding: TranscodingConfig = Field(default_factory=TranscodingConfig)
    hardware: HardwareConfig = Field(default_factory=HardwareConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)


def find_config_file() -> Optional[Path]:
    """Find the configuration file in standard locations."""
    search_paths = [
        Path.cwd() / "ghoststream.yaml",
        Path.cwd() / "ghoststream.yml",
        Path.cwd() / "config" / "ghoststream.yaml",
        Path.home() / ".config" / "ghoststream" / "ghoststream.yaml",
        Path("/etc/ghoststream/ghoststream.yaml"),
    ]
    
    for path in search_paths:
        if path.exists():
            return path
    
    return None


def load_config(config_path: Optional[str] = None) -> GhostStreamConfig:
    """Load configuration from YAML file or use defaults."""
    config_file = Path(config_path) if config_path else find_config_file()
    
    if config_file and config_file.exists():
        with open(config_file, "r") as f:
            yaml_data = yaml.safe_load(f) or {}
        return GhostStreamConfig(**yaml_data)
    
    return GhostStreamConfig()


# Global config instance
_config: Optional[GhostStreamConfig] = None


def get_config() -> GhostStreamConfig:
    """Get the global configuration instance."""
    global _config
    if _config is None:
        _config = load_config()
    return _config


def set_config(config: GhostStreamConfig) -> None:
    """Set the global configuration instance."""
    global _config
    _config = config

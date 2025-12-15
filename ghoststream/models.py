"""
Pydantic models for GhostStream API
"""

from typing import Optional, List, Dict, Any
from enum import Enum
from pydantic import BaseModel, Field
from datetime import datetime


class TranscodeMode(str, Enum):
    STREAM = "stream"      # Single quality HLS streaming
    BATCH = "batch"        # File-to-file transcoding
    ABR = "abr"            # Adaptive bitrate streaming (multiple qualities)


class OutputFormat(str, Enum):
    HLS = "hls"
    DASH = "dash"
    MP4 = "mp4"
    WEBM = "webm"
    MKV = "mkv"


class VideoCodec(str, Enum):
    H264 = "h264"
    H265 = "h265"
    VP9 = "vp9"
    AV1 = "av1"
    COPY = "copy"


class AudioCodec(str, Enum):
    AAC = "aac"
    OPUS = "opus"
    MP3 = "mp3"
    FLAC = "flac"
    AC3 = "ac3"
    COPY = "copy"


class Resolution(str, Enum):
    UHD_4K = "4k"
    FHD_1080P = "1080p"
    HD_720P = "720p"
    SD_480P = "480p"
    ORIGINAL = "original"


class HWAccel(str, Enum):
    AUTO = "auto"
    NVENC = "nvenc"
    QSV = "qsv"
    VAAPI = "vaapi"
    VIDEOTOOLBOX = "videotoolbox"
    AMF = "amf"
    SOFTWARE = "software"


class JobStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    READY = "ready"
    ERROR = "error"
    CANCELLED = "cancelled"


class OutputConfig(BaseModel):
    format: OutputFormat = OutputFormat.HLS
    video_codec: VideoCodec = VideoCodec.H264
    audio_codec: AudioCodec = AudioCodec.AAC
    resolution: Resolution = Resolution.ORIGINAL
    bitrate: str = "auto"
    hw_accel: HWAccel = HWAccel.AUTO
    # Advanced options
    two_pass: bool = Field(default=False, description="Use two-pass encoding for better quality (batch only)")
    tone_map: bool = Field(default=True, description="Convert HDR to SDR automatically")
    max_audio_channels: int = Field(default=2, description="Max audio channels (2=stereo, 6=5.1)")


class TranscodeRequest(BaseModel):
    source: str = Field(..., description="Source file URL or path")
    mode: TranscodeMode = TranscodeMode.STREAM
    output: OutputConfig = Field(default_factory=OutputConfig)
    start_time: float = Field(default=0, description="Start position in seconds")
    callback_url: Optional[str] = Field(default=None, description="URL to call when job completes")
    session_id: Optional[str] = Field(default=None, description="Viewer session ID for stream sharing")


class TranscodeResponse(BaseModel):
    job_id: str
    status: JobStatus
    progress: float = 0
    stream_url: Optional[str] = None
    download_url: Optional[str] = None
    eta_seconds: Optional[int] = None
    hw_accel_used: Optional[str] = None
    error_message: Optional[str] = None
    # Extended info
    variants: Optional[List[Dict[str, Any]]] = Field(default=None, description="ABR quality variants")
    media_info: Optional[Dict[str, Any]] = Field(default=None, description="Source media information")


class JobStatusResponse(BaseModel):
    job_id: str
    status: JobStatus
    progress: float
    current_time: Optional[float] = None
    duration: Optional[float] = None
    stream_url: Optional[str] = None
    download_url: Optional[str] = None
    eta_seconds: Optional[int] = None
    hw_accel_used: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class HealthResponse(BaseModel):
    status: str = "healthy"
    version: str
    uptime_seconds: float
    current_jobs: int
    queued_jobs: int


class StatsResponse(BaseModel):
    total_jobs_processed: int
    successful_jobs: int
    failed_jobs: int
    cancelled_jobs: int
    current_queue_length: int
    active_jobs: int
    average_transcode_speed: float
    total_bytes_processed: int
    uptime_seconds: float
    hw_accel_usage: Dict[str, int]


class CapabilitiesResponse(BaseModel):
    hw_accels: List[Dict[str, Any]]
    video_codecs: List[str]
    audio_codecs: List[str]
    formats: List[str]
    max_concurrent_jobs: int
    ffmpeg_version: str
    platform: str


class WebSocketMessage(BaseModel):
    type: str  # "progress", "status_change", "error"
    job_id: str
    data: Dict[str, Any]

"""
FFmpeg error classification for proper retry/fallback decisions.

Provides structured error categorization to determine whether to:
- Retry with same configuration (transient errors)
- Fall back to software encoding (hardware errors)
- Fail immediately (fatal errors)
"""

import logging
from dataclasses import dataclass
from typing import List, Tuple, Optional

logger = logging.getLogger(__name__)


@dataclass
class FFmpegError:
    """Represents a classified FFmpeg error."""
    pattern: str
    category: str  # 'hardware', 'transient', 'fatal', 'resource'
    retryable: bool
    description: str


# Comprehensive FFmpeg error map for intelligent error handling
FFMPEG_ERROR_MAP: List[FFmpegError] = [
    # Hardware errors - trigger fallback to software
    FFmpegError("no capable devices found", "hardware", False, "No hardware encoder devices"),
    FFmpegError("cannot open", "hardware", False, "Cannot open hardware device"),
    FFmpegError("initialization failed", "hardware", False, "Hardware init failed"),
    FFmpegError("hw_frames_ctx", "hardware", False, "Hardware frame context error"),
    FFmpegError("hwaccel", "hardware", False, "Hardware acceleration error"),
    FFmpegError("cuda error", "hardware", False, "CUDA error"),
    FFmpegError("nvenc", "hardware", False, "NVENC error"),
    FFmpegError("qsv", "hardware", False, "QuickSync error"),
    FFmpegError("vaapi", "hardware", False, "VAAPI error"),
    FFmpegError("videotoolbox", "hardware", False, "VideoToolbox error"),
    FFmpegError("amf", "hardware", False, "AMF error"),
    FFmpegError("gpu", "hardware", False, "GPU error"),
    FFmpegError("device", "hardware", False, "Device error"),
    FFmpegError("driver", "hardware", False, "Driver error"),
    FFmpegError("exceeds level limit", "hardware", False, "Encoding level exceeded"),
    FFmpegError("encode session", "hardware", False, "NVENC session limit"),
    
    # Transient network errors - retry with backoff
    FFmpegError("connection refused", "transient", True, "Connection refused"),
    FFmpegError("connection reset", "transient", True, "Connection reset"),
    FFmpegError("connection timed out", "transient", True, "Connection timeout"),
    FFmpegError("timeout", "transient", True, "Operation timeout"),
    FFmpegError("temporarily unavailable", "transient", True, "Resource temporarily unavailable"),
    FFmpegError("network is unreachable", "transient", True, "Network unreachable"),
    FFmpegError("no route to host", "transient", True, "No route to host"),
    FFmpegError("end of file", "transient", True, "Unexpected end of file"),
    FFmpegError("server returned", "transient", True, "HTTP server error"),
    FFmpegError("404 not found", "transient", False, "Resource not found"),
    FFmpegError("403 forbidden", "transient", False, "Access forbidden"),
    FFmpegError("broken pipe", "transient", True, "Broken pipe"),
    FFmpegError("ssl", "transient", True, "SSL/TLS error"),
    
    # Resource errors - may retry after delay
    FFmpegError("out of memory", "resource", False, "Out of memory"),
    FFmpegError("cannot allocate", "resource", True, "Memory allocation failed"),
    FFmpegError("too many open files", "resource", True, "File descriptor limit"),
    FFmpegError("no space left", "resource", False, "No disk space"),
    FFmpegError("disk quota", "resource", False, "Disk quota exceeded"),
    
    # Fatal errors - do not retry
    FFmpegError("invalid data", "fatal", False, "Invalid input data"),
    FFmpegError("invalid argument", "fatal", False, "Invalid argument"),
    FFmpegError("no such file", "fatal", False, "File not found"),
    FFmpegError("permission denied", "fatal", False, "Permission denied"),
    FFmpegError("codec not found", "fatal", False, "Codec not found"),
    FFmpegError("encoder not found", "fatal", False, "Encoder not found"),
    FFmpegError("decoder not found", "fatal", False, "Decoder not found"),
    FFmpegError("filter not found", "fatal", False, "Filter not found"),
    FFmpegError("moov atom not found", "fatal", False, "Invalid MP4 file"),
]


class ErrorClassifier:
    """Classifies FFmpeg errors for intelligent retry/fallback decisions."""
    
    def __init__(self, error_map: Optional[List[FFmpegError]] = None):
        self.error_map = error_map or FFMPEG_ERROR_MAP
    
    def classify(self, error_msg: str) -> Tuple[Optional[FFmpegError], str]:
        """
        Classify FFmpeg error using the comprehensive error map.
        
        Args:
            error_msg: The error message from FFmpeg stderr
            
        Returns:
            Tuple of (matched_error, category). Category is 'unknown' if no match.
        """
        error_lower = error_msg.lower()
        
        for error in self.error_map:
            if error.pattern in error_lower:
                return error, error.category
        
        return None, "unknown"
    
    def is_hardware_error(self, error_msg: str) -> bool:
        """Check if error is hardware-related."""
        error, category = self.classify(error_msg)
        return category == "hardware"
    
    def is_transient_error(self, error_msg: str) -> bool:
        """Check if error is transient (retryable)."""
        error, category = self.classify(error_msg)
        if error:
            return error.retryable
        return False
    
    def is_fatal_error(self, error_msg: str) -> bool:
        """Check if error is fatal (no retry)."""
        error, category = self.classify(error_msg)
        return category == "fatal"
    
    def is_resource_error(self, error_msg: str) -> bool:
        """Check if error is resource-related."""
        error, category = self.classify(error_msg)
        return category == "resource"
    
    def get_error_description(self, error_msg: str) -> str:
        """Get human-readable description of the error."""
        error, category = self.classify(error_msg)
        if error:
            return error.description
        return "Unknown error"
    
    def should_retry(self, error_msg: str, attempt: int, max_retries: int) -> bool:
        """
        Determine if the operation should be retried.
        
        Args:
            error_msg: The error message
            attempt: Current attempt number (0-indexed)
            max_retries: Maximum number of retries allowed
            
        Returns:
            True if should retry, False otherwise
        """
        error, category = self.classify(error_msg)
        
        # Fatal errors never retry
        if category == "fatal":
            return False
        
        # Hardware errors don't retry (but may fallback)
        if category == "hardware":
            return False
        
        # Transient errors retry if within limit
        if error and error.retryable:
            return attempt < max_retries
        
        # Resource errors may retry with a delay
        if category == "resource" and error and error.retryable:
            return attempt < max_retries
        
        # Unknown errors get limited retries
        if category == "unknown":
            return attempt < min(max_retries, 1)  # At most 1 retry for unknown
        
        return False
    
    def should_fallback_to_software(self, error_msg: str) -> bool:
        """Determine if should fallback to software encoding."""
        return self.is_hardware_error(error_msg)


# Global classifier instance
_classifier: Optional[ErrorClassifier] = None


def get_error_classifier() -> ErrorClassifier:
    """Get or create the global error classifier."""
    global _classifier
    if _classifier is None:
        _classifier = ErrorClassifier()
    return _classifier

"""
Video and audio filter building for FFmpeg.
Handles HDR to SDR tone mapping, scaling, and format conversion.
"""

import subprocess
import logging
from typing import List, Optional

from ..models import OutputConfig, VideoCodec, Resolution
from .models import MediaInfo
from .constants import (
    TONEMAP_FILTER,
    TONEMAP_FILTER_SIMPLE,
    get_resolution_map,
)

logger = logging.getLogger(__name__)


class FilterBuilder:
    """Builds FFmpeg video and audio filter chains."""
    
    def __init__(self, ffmpeg_path: str = "ffmpeg"):
        self.ffmpeg_path = ffmpeg_path
        self._filter_cache: dict = {}
    
    def check_filter_available(self, filter_name: str) -> bool:
        """Check if an FFmpeg filter is available."""
        if filter_name in self._filter_cache:
            return self._filter_cache[filter_name]
        
        try:
            result = subprocess.run(
                [self.ffmpeg_path, "-filters", "-hide_banner"],
                capture_output=True, text=True, timeout=10
            )
            available = filter_name in result.stdout
            self._filter_cache[filter_name] = available
            return available
        except:
            return False
    
    def get_tonemap_filter(self, use_zscale: bool = True) -> str:
        """Get the appropriate tone mapping filter for HDR to SDR conversion."""
        if use_zscale and self.check_filter_available("zscale"):
            return TONEMAP_FILTER
        else:
            logger.warning("[Filters] zscale not available, using simple tonemap")
            return TONEMAP_FILTER_SIMPLE
    
    def get_scale_filter(
        self,
        resolution: Resolution,
        source_width: int,
        source_height: int
    ) -> Optional[str]:
        """Get the scale filter for the target resolution."""
        if resolution == Resolution.ORIGINAL:
            return None
        
        target = get_resolution_map().get(resolution)
        if not target:
            return None
        
        target_width, target_height = target
        
        # Don't upscale
        if source_width <= target_width and source_height <= target_height:
            return None
        
        # Scale to fit within target, maintaining aspect ratio
        return f"scale='min({target_width},iw)':'min({target_height},ih)':force_original_aspect_ratio=decrease"
    
    def needs_tonemap(
        self,
        media_info: Optional[MediaInfo],
        output_config: OutputConfig,
        enable_tonemap: bool = True
    ) -> bool:
        """Determine if HDR to SDR tone mapping is needed."""
        return (
            enable_tonemap and
            media_info is not None and
            media_info.is_hdr and
            output_config.video_codec != VideoCodec.H265 and
            output_config.video_codec != VideoCodec.COPY
        )
    
    def build_video_filters(
        self,
        media_info: Optional[MediaInfo],
        output_config: OutputConfig,
        video_encoder: str,
        enable_tonemap: bool = True
    ) -> List[str]:
        """Build video filter chain with HDR handling."""
        vf_filters = []
        
        # HDR to SDR tone mapping (if source is HDR and we're not using HEVC output)
        needs_tm = self.needs_tonemap(media_info, output_config, enable_tonemap)
        
        if needs_tm:
            logger.info("[Filters] Applying HDR to SDR tone mapping")
            vf_filters.append(self.get_tonemap_filter())
        
        # Scale filter if needed
        if media_info and output_config.resolution != Resolution.ORIGINAL:
            scale_filter = self.get_scale_filter(
                output_config.resolution,
                media_info.width,
                media_info.height
            )
            if scale_filter:
                vf_filters.append(scale_filter)
        
        # Force 8-bit pixel format for h264 encoders
        # libx264 needs yuv420p; hardware encoders (nvenc/qsv/amf/vaapi) need nv12
        # Always add at the end to ensure correct format after tonemap+scale chain
        if "h264" in video_encoder and video_encoder != "copy":
            if video_encoder == "libx264":
                vf_filters.append("format=yuv420p")
            else:
                vf_filters.append("format=nv12")
        
        return vf_filters
    
    def build_abr_filter_complex(
        self,
        variants: list,
        media_info: MediaInfo,
        needs_tonemap: bool
    ) -> List[str]:
        """Build filter_complex for ABR multi-output encoding."""
        if not variants:
            return []
        
        filter_parts = []
        num_variants = len(variants)
        
        # Split input stream for multiple outputs
        split_outputs = "".join(f"[s{i}]" for i in range(num_variants))
        
        if needs_tonemap:
            tonemap = self.get_tonemap_filter()
            filter_parts.append(f"[0:v]{tonemap},split={num_variants}{split_outputs}")
        else:
            filter_parts.append(f"[0:v]split={num_variants}{split_outputs}")
        
        # Scale each split output to variant resolution
        for i, variant in enumerate(variants):
            # Use scale with pad to ensure even dimensions (required for H.264)
            scale = f"scale={variant.width}:{variant.height}:force_original_aspect_ratio=decrease"
            pad = f"pad={variant.width}:{variant.height}:(ow-iw)/2:(oh-ih)/2"
            filter_chain = f"[s{i}]{scale},{pad},format=yuv420p[v{i}]"
            filter_parts.append(filter_chain)
        
        return filter_parts

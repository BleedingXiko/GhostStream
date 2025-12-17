"""
Adaptive hardware-aware transcoding for GhostStream.
Enterprise-grade adaptive quality with:
- Real-time system load monitoring
- Thermal throttling awareness  
- Dynamic quality adjustment based on concurrent users
- Network bandwidth consideration
- Queue management with priority
"""

import os
import platform
import subprocess
import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple, Dict, Any
from datetime import datetime, timedelta

from ..hardware import Capabilities, HWAccelType, GPUInfo
from .models import QualityPreset, MediaInfo
from .constants import QUALITY_LADDER

logger = logging.getLogger(__name__)


class HardwareTier(str, Enum):
    """Hardware capability tiers for adaptive transcoding."""
    ULTRA = "ultra"      # High-end desktop GPU (RTX 3080+, 8GB+ VRAM)
    HIGH = "high"        # Mid-range desktop GPU (RTX 3060+, 6GB+ VRAM)
    MEDIUM = "medium"    # Entry GPU or laptop GPU (GTX 1650+, 4GB+ VRAM)
    LOW = "low"          # Integrated/weak GPU or software only
    MINIMAL = "minimal"  # Very limited hardware, basic transcoding only


class PowerSource(str, Enum):
    """Power source detection for laptops."""
    AC = "ac"
    BATTERY = "battery"
    UNKNOWN = "unknown"


@dataclass
class CPUInfo:
    """CPU information for encoding decisions."""
    name: str = ""
    cores: int = 1
    threads: int = 1
    frequency_mhz: int = 0
    is_mobile: bool = False
    
    @property
    def encoding_power(self) -> int:
        """Estimate encoding power score (1-100)."""
        # Base score from thread count
        base = min(self.threads * 5, 50)
        # Bonus for high frequency
        freq_bonus = min((self.frequency_mhz - 2000) / 100, 25) if self.frequency_mhz > 2000 else 0
        # Penalty for mobile
        mobile_penalty = 15 if self.is_mobile else 0
        return max(10, int(base + freq_bonus - mobile_penalty))


@dataclass
class SystemProfile:
    """Complete system profile for adaptive decisions."""
    cpu: CPUInfo = field(default_factory=CPUInfo)
    gpu_vram_mb: int = 0
    gpu_name: str = ""
    total_ram_mb: int = 0
    is_laptop: bool = False
    power_source: PowerSource = PowerSource.UNKNOWN
    platform: str = ""
    
    # Computed limits
    tier: HardwareTier = HardwareTier.LOW
    max_resolution: Tuple[int, int] = (1920, 1080)
    max_bitrate_mbps: float = 8.0
    max_concurrent_jobs: int = 1
    recommended_encoder: str = "libx264"


class HardwareProfiler:
    """Profiles system hardware for adaptive transcoding decisions."""
    
    def __init__(self, capabilities: Capabilities):
        self.capabilities = capabilities
        self._profile: Optional[SystemProfile] = None
    
    def get_profile(self, force_refresh: bool = False) -> SystemProfile:
        """Get or create system profile."""
        if self._profile is None or force_refresh:
            self._profile = self._build_profile()
        return self._profile
    
    def _build_profile(self) -> SystemProfile:
        """Build complete system profile."""
        profile = SystemProfile(platform=platform.system())
        
        # Detect CPU
        profile.cpu = self._detect_cpu()
        
        # Detect RAM
        profile.total_ram_mb = self._detect_ram()
        
        # Detect GPU from capabilities
        profile.gpu_vram_mb, profile.gpu_name = self._get_gpu_info()
        
        # Detect if laptop
        profile.is_laptop = self._detect_laptop()
        
        # Detect power source
        profile.power_source = self._detect_power_source()
        
        # Calculate tier and limits
        profile.tier = self._calculate_tier(profile)
        profile.max_resolution = self._get_max_resolution(profile.tier)
        profile.max_bitrate_mbps = self._get_max_bitrate(profile.tier)
        profile.max_concurrent_jobs = self._get_max_jobs(profile)
        profile.recommended_encoder = self._get_recommended_encoder(profile)
        
        logger.info(f"[Adaptive] Hardware Profile: Tier={profile.tier.value}, "
                   f"GPU={profile.gpu_name or 'None'}, VRAM={profile.gpu_vram_mb}MB, "
                   f"Laptop={profile.is_laptop}, Power={profile.power_source.value}")
        logger.info(f"[Adaptive] Limits: Max {profile.max_resolution[0]}x{profile.max_resolution[1]}, "
                   f"{profile.max_bitrate_mbps}Mbps, {profile.max_concurrent_jobs} jobs")
        
        return profile
    
    def _detect_cpu(self) -> CPUInfo:
        """Detect CPU information."""
        cpu = CPUInfo()
        
        try:
            cpu.cores = os.cpu_count() or 1
            cpu.threads = cpu.cores  # Assume HT, will be refined
            
            if platform.system() == "Windows":
                # Windows: use wmic
                try:
                    result = subprocess.run(
                        ["wmic", "cpu", "get", "Name,NumberOfCores,NumberOfLogicalProcessors,MaxClockSpeed", "/format:csv"],
                        capture_output=True, text=True, timeout=10
                    )
                    if result.returncode == 0:
                        lines = [l.strip() for l in result.stdout.strip().split('\n') if l.strip()]
                        if len(lines) >= 2:
                            parts = lines[-1].split(',')
                            if len(parts) >= 4:
                                cpu.frequency_mhz = int(parts[1]) if parts[1].isdigit() else 0
                                cpu.name = parts[2]
                                cpu.cores = int(parts[3]) if parts[3].isdigit() else cpu.cores
                                cpu.threads = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else cpu.cores
                except Exception as e:
                    logger.debug(f"Failed to get CPU info via wmic: {e}")
                
            elif platform.system() == "Linux":
                # Linux: parse /proc/cpuinfo
                try:
                    with open("/proc/cpuinfo") as f:
                        content = f.read()
                        
                    # Get model name
                    for line in content.split('\n'):
                        if line.startswith('model name'):
                            cpu.name = line.split(':')[1].strip()
                            break
                    
                    # Count processors
                    cpu.threads = content.count('processor\t:')
                    
                    # Get frequency
                    for line in content.split('\n'):
                        if 'cpu MHz' in line:
                            cpu.frequency_mhz = int(float(line.split(':')[1].strip()))
                            break
                except Exception as e:
                    logger.debug(f"Failed to parse /proc/cpuinfo: {e}")
                    
            elif platform.system() == "Darwin":
                # macOS: use sysctl
                try:
                    result = subprocess.run(
                        ["sysctl", "-n", "machdep.cpu.brand_string"],
                        capture_output=True, text=True, timeout=5
                    )
                    if result.returncode == 0:
                        cpu.name = result.stdout.strip()
                    
                    result = subprocess.run(
                        ["sysctl", "-n", "hw.ncpu"],
                        capture_output=True, text=True, timeout=5
                    )
                    if result.returncode == 0:
                        cpu.threads = int(result.stdout.strip())
                except Exception as e:
                    logger.debug(f"Failed to get CPU info via sysctl: {e}")
            
            # Detect mobile CPU
            mobile_indicators = ['mobile', 'laptop', 'u ', ' u', 'low power', 'atom', 'm3', 'm5', 'm7']
            cpu.is_mobile = any(ind in cpu.name.lower() for ind in mobile_indicators)
            
        except Exception as e:
            logger.warning(f"[Adaptive] CPU detection failed: {e}")
        
        return cpu
    
    def _detect_ram(self) -> int:
        """Detect total system RAM in MB."""
        try:
            if platform.system() == "Windows":
                result = subprocess.run(
                    ["wmic", "computersystem", "get", "TotalPhysicalMemory", "/format:csv"],
                    capture_output=True, text=True, timeout=10
                )
                if result.returncode == 0:
                    lines = [l.strip() for l in result.stdout.strip().split('\n') if l.strip()]
                    if len(lines) >= 2:
                        parts = lines[-1].split(',')
                        if len(parts) >= 2 and parts[1].isdigit():
                            return int(int(parts[1]) / (1024 * 1024))
                            
            elif platform.system() == "Linux":
                with open("/proc/meminfo") as f:
                    for line in f:
                        if line.startswith("MemTotal:"):
                            return int(line.split()[1]) // 1024
                            
            elif platform.system() == "Darwin":
                result = subprocess.run(
                    ["sysctl", "-n", "hw.memsize"],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0:
                    return int(result.stdout.strip()) // (1024 * 1024)
        except Exception as e:
            logger.debug(f"Failed to detect RAM: {e}")
        
        return 8192  # Default 8GB
    
    def _get_gpu_info(self) -> Tuple[int, str]:
        """Extract GPU info from capabilities."""
        for hw in self.capabilities.hw_accels:
            if hw.gpu_info and hw.available:
                return hw.gpu_info.memory_mb, hw.gpu_info.name
        return 0, ""
    
    def _detect_laptop(self) -> bool:
        """Detect if running on a laptop."""
        try:
            if platform.system() == "Windows":
                # Check for battery
                result = subprocess.run(
                    ["wmic", "path", "Win32_Battery", "get", "BatteryStatus"],
                    capture_output=True, text=True, timeout=10
                )
                return "BatteryStatus" in result.stdout
                
            elif platform.system() == "Linux":
                # Check for battery in /sys
                import os
                battery_paths = [
                    "/sys/class/power_supply/BAT0",
                    "/sys/class/power_supply/BAT1",
                    "/sys/class/power_supply/battery",
                ]
                return any(os.path.exists(p) for p in battery_paths)
                
            elif platform.system() == "Darwin":
                # Check pmset for battery
                result = subprocess.run(
                    ["pmset", "-g", "batt"],
                    capture_output=True, text=True, timeout=5
                )
                return "Battery" in result.stdout or "InternalBattery" in result.stdout
        except Exception:
            pass
        
        return False
    
    def _detect_power_source(self) -> PowerSource:
        """Detect current power source."""
        try:
            if platform.system() == "Windows":
                result = subprocess.run(
                    ["wmic", "path", "Win32_Battery", "get", "BatteryStatus", "/format:csv"],
                    capture_output=True, text=True, timeout=10
                )
                if result.returncode == 0 and "2" in result.stdout:  # 2 = AC power
                    return PowerSource.AC
                elif "BatteryStatus" in result.stdout:
                    return PowerSource.BATTERY
                    
            elif platform.system() == "Linux":
                status_path = "/sys/class/power_supply/AC/online"
                if os.path.exists(status_path):
                    with open(status_path) as f:
                        return PowerSource.AC if f.read().strip() == "1" else PowerSource.BATTERY
                        
            elif platform.system() == "Darwin":
                result = subprocess.run(
                    ["pmset", "-g", "batt"],
                    capture_output=True, text=True, timeout=5
                )
                if "AC Power" in result.stdout:
                    return PowerSource.AC
                elif "Battery Power" in result.stdout:
                    return PowerSource.BATTERY
        except Exception:
            pass
        
        return PowerSource.UNKNOWN
    
    def _calculate_tier(self, profile: SystemProfile) -> HardwareTier:
        """Calculate hardware tier based on detected capabilities."""
        # Check for available hardware encoders
        has_hw_encoder = any(
            hw.available and hw.type != HWAccelType.SOFTWARE 
            for hw in self.capabilities.hw_accels
        )
        
        vram = profile.gpu_vram_mb
        cpu_power = profile.cpu.encoding_power
        
        # On battery, drop one tier
        battery_penalty = profile.power_source == PowerSource.BATTERY
        
        # Determine base tier
        if has_hw_encoder:
            if vram >= 8000:  # 8GB+ VRAM
                tier = HardwareTier.ULTRA
            elif vram >= 6000:  # 6GB+ VRAM
                tier = HardwareTier.HIGH
            elif vram >= 4000:  # 4GB+ VRAM
                tier = HardwareTier.MEDIUM
            elif vram >= 2000:  # 2GB+ VRAM
                tier = HardwareTier.LOW
            else:
                tier = HardwareTier.MINIMAL
        else:
            # Software only - base on CPU
            if cpu_power >= 70:
                tier = HardwareTier.MEDIUM
            elif cpu_power >= 40:
                tier = HardwareTier.LOW
            else:
                tier = HardwareTier.MINIMAL
        
        # Laptop penalty
        if profile.is_laptop and tier in (HardwareTier.ULTRA, HardwareTier.HIGH):
            tier = HardwareTier.MEDIUM
        
        # Battery penalty
        if battery_penalty:
            tier_order = [HardwareTier.MINIMAL, HardwareTier.LOW, HardwareTier.MEDIUM, 
                         HardwareTier.HIGH, HardwareTier.ULTRA]
            idx = tier_order.index(tier)
            if idx > 0:
                tier = tier_order[idx - 1]
        
        return tier
    
    def _get_max_resolution(self, tier: HardwareTier) -> Tuple[int, int]:
        """Get maximum recommended resolution for tier."""
        limits = {
            HardwareTier.ULTRA: (3840, 2160),   # 4K
            HardwareTier.HIGH: (2560, 1440),    # 1440p
            HardwareTier.MEDIUM: (1920, 1080),  # 1080p
            HardwareTier.LOW: (1280, 720),      # 720p
            HardwareTier.MINIMAL: (854, 480),   # 480p
        }
        return limits.get(tier, (1920, 1080))
    
    def _get_max_bitrate(self, tier: HardwareTier) -> float:
        """Get maximum recommended bitrate in Mbps for tier."""
        limits = {
            HardwareTier.ULTRA: 25.0,
            HardwareTier.HIGH: 15.0,
            HardwareTier.MEDIUM: 8.0,
            HardwareTier.LOW: 4.0,
            HardwareTier.MINIMAL: 2.0,
        }
        return limits.get(tier, 8.0)
    
    def _get_max_jobs(self, profile: SystemProfile) -> int:
        """Get maximum concurrent transcoding jobs."""
        # Base on tier
        tier_jobs = {
            HardwareTier.ULTRA: 4,
            HardwareTier.HIGH: 3,
            HardwareTier.MEDIUM: 2,
            HardwareTier.LOW: 1,
            HardwareTier.MINIMAL: 1,
        }
        jobs = tier_jobs.get(profile.tier, 1)
        
        # On battery, limit to 1
        if profile.power_source == PowerSource.BATTERY:
            jobs = 1
        
        return jobs
    
    def _get_recommended_encoder(self, profile: SystemProfile) -> str:
        """Get recommended encoder for this hardware."""
        best_accel = self.capabilities.get_best_hw_accel()
        
        encoder_map = {
            HWAccelType.NVENC: "h264_nvenc",
            HWAccelType.QSV: "h264_qsv",
            HWAccelType.VAAPI: "h264_vaapi",
            HWAccelType.AMF: "h264_amf",
            HWAccelType.VIDEOTOOLBOX: "h264_videotoolbox",
            HWAccelType.SOFTWARE: "libx264",
        }
        
        return encoder_map.get(best_accel, "libx264")


class AdaptiveQualitySelector:
    """Selects optimal quality settings based on hardware and source media."""
    
    def __init__(self, profile: SystemProfile):
        self.profile = profile
    
    def get_max_abr_variants(self) -> int:
        """
        Get maximum number of ABR variants based on hardware tier.
        
        Consumer NVIDIA GPUs have NVENC session limits:
        - GeForce cards: typically 2-3 concurrent encode sessions
        - Quadro/RTX professional: 8+ sessions
        - Laptops: often more restricted due to thermals
        
        We use conservative limits to ensure reliability.
        """
        tier_variants = {
            HardwareTier.ULTRA: 4,    # High-end desktop, likely Quadro/RTX
            HardwareTier.HIGH: 3,     # Good desktop GPU
            HardwareTier.MEDIUM: 2,   # Entry GPU or laptop - NVENC limit!
            HardwareTier.LOW: 1,      # Weak GPU, single stream only
            HardwareTier.MINIMAL: 1,  # Very limited
        }
        
        max_variants = tier_variants.get(self.profile.tier, 2)
        
        # Extra caution for laptops due to thermals and NVENC restrictions
        if self.profile.is_laptop and max_variants > 2:
            max_variants = 2
            
        return max_variants
    
    def get_optimal_presets(self, media_info: MediaInfo) -> List[QualityPreset]:
        """Get optimal quality presets for the source media given hardware limits."""
        max_width, max_height = self.profile.max_resolution
        max_bitrate = self.profile.max_bitrate_mbps
        
        # Filter presets based on hardware limits
        valid_presets = []
        for preset in QUALITY_LADDER:
            # Skip presets above hardware limit
            if preset.width > max_width or preset.height > max_height:
                continue
            
            # Skip presets with bitrate above limit
            preset_bitrate = self._parse_bitrate(preset.video_bitrate)
            if preset_bitrate > max_bitrate:
                continue
            
            # Skip presets above source resolution (no upscaling)
            if preset.width > media_info.width or preset.height > media_info.height:
                continue
            
            valid_presets.append(preset)
        
        # Ensure at least one preset
        if not valid_presets:
            # Create a minimal preset based on limits
            valid_presets.append(QualityPreset(
                name="adaptive",
                width=min(max_width, media_info.width),
                height=min(max_height, media_info.height),
                video_bitrate=f"{max_bitrate}M",
                audio_bitrate="128k",
                crf=23,
                hw_preset="p4"
            ))
            return valid_presets
            
        # Limit variants based on hardware capability (NVENC session limits, etc.)
        max_variants = self.get_max_abr_variants()
        
        if len(valid_presets) <= max_variants:
            return valid_presets
            
        # Select variants with good spread up to max_variants
        selected = []
        
        # Always include highest quality
        selected.append(valid_presets[0])
        
        # Always include lowest quality (if we have room)
        if max_variants >= 2:
            selected.append(valid_presets[-1])
        
        # Add middle variants if we have room
        if max_variants >= 3:
            mid_high_idx = len(valid_presets) // 3
            if valid_presets[mid_high_idx] not in selected:
                selected.append(valid_presets[mid_high_idx])
            
        if max_variants >= 4:
            mid_low_idx = (len(valid_presets) * 2) // 3
            if valid_presets[mid_low_idx] not in selected:
                selected.append(valid_presets[mid_low_idx])
            
        # Restore sort order (High -> Low)
        selected.sort(key=lambda x: x.width, reverse=True)
        
        return selected
    
    def get_single_best_preset(self, media_info: MediaInfo) -> QualityPreset:
        """Get the single best quality preset for transcoding."""
        presets = self.get_optimal_presets(media_info)
        
        # Return highest quality valid preset
        if presets:
            return presets[0]
        
        # Fallback
        return QUALITY_LADDER[-1]  # Lowest quality
    
    def should_transcode(self, media_info: MediaInfo) -> Tuple[bool, str]:
        """
        Determine if transcoding is needed and why.
        Returns (should_transcode, reason).
        """
        max_width, max_height = self.profile.max_resolution
        
        # Check if source exceeds hardware limits
        if media_info.width > max_width or media_info.height > max_height:
            return True, f"Source {media_info.width}x{media_info.height} exceeds hardware limit {max_width}x{max_height}"
        
        # Check if HDR needs tone mapping (most displays are SDR)
        if media_info.is_hdr:
            return True, "HDR content needs tone mapping for SDR display"
        
        # Check for codec compatibility
        incompatible_codecs = ["hevc", "h265", "av1", "vp9"]
        if media_info.video_codec.lower() in incompatible_codecs:
            return True, f"Codec {media_info.video_codec} may need transcoding for compatibility"
        
        return False, "Direct stream compatible"
    
    def _parse_bitrate(self, bitrate_str: str) -> float:
        """Parse bitrate string to Mbps."""
        bitrate_str = bitrate_str.upper().strip()
        if bitrate_str.endswith('M'):
            return float(bitrate_str[:-1])
        elif bitrate_str.endswith('K'):
            return float(bitrate_str[:-1]) / 1000
        return float(bitrate_str) / 1_000_000


# =============================================================================
# ENTERPRISE REAL-TIME MONITORING & LOAD MANAGEMENT
# =============================================================================

@dataclass
class SystemMetrics:
    """Real-time system resource metrics."""
    timestamp: datetime = field(default_factory=datetime.now)
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    gpu_percent: float = 0.0
    gpu_memory_percent: float = 0.0
    gpu_temperature_c: float = 0.0
    disk_io_percent: float = 0.0
    network_bandwidth_mbps: float = 0.0
    active_transcode_jobs: int = 0
    queued_jobs: int = 0
    
    @property
    def is_overloaded(self) -> bool:
        """Check if system is overloaded."""
        return (
            self.cpu_percent > 90 or
            self.memory_percent > 85 or
            self.gpu_percent > 95 or
            self.gpu_temperature_c > 85
        )
    
    @property
    def load_factor(self) -> float:
        """Get overall load factor (0.0 = idle, 1.0 = fully loaded)."""
        weights = {
            'cpu': 0.3,
            'memory': 0.2,
            'gpu': 0.4,
            'gpu_temp': 0.1,
        }
        
        cpu_load = min(self.cpu_percent / 100, 1.0)
        mem_load = min(self.memory_percent / 100, 1.0)
        gpu_load = min(self.gpu_percent / 100, 1.0)
        temp_load = min(max(self.gpu_temperature_c - 60, 0) / 30, 1.0)  # 60-90C range
        
        return (
            weights['cpu'] * cpu_load +
            weights['memory'] * mem_load +
            weights['gpu'] * gpu_load +
            weights['gpu_temp'] * temp_load
        )


class SystemMonitor:
    """Real-time system resource monitoring for adaptive transcoding."""
    
    def __init__(self):
        self._metrics = SystemMetrics()
        self._lock = threading.Lock()
        self._running = False
        self._monitor_thread: Optional[threading.Thread] = None
        self._history: List[SystemMetrics] = []
        self._max_history = 60  # Keep 60 samples (1 minute at 1 sample/sec)
    
    def start(self):
        """Start background monitoring."""
        if self._running:
            return
        
        self._running = True
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()
        logger.info("[Monitor] System monitoring started")
    
    def stop(self):
        """Stop background monitoring."""
        self._running = False
        if self._monitor_thread:
            self._monitor_thread.join(timeout=2.0)
        logger.info("[Monitor] System monitoring stopped")
    
    def _monitor_loop(self):
        """Background monitoring loop."""
        while self._running:
            try:
                metrics = self._collect_metrics()
                with self._lock:
                    self._metrics = metrics
                    self._history.append(metrics)
                    if len(self._history) > self._max_history:
                        self._history.pop(0)
            except Exception as e:
                logger.debug(f"[Monitor] Collection error: {e}")
            
            time.sleep(1.0)  # Sample every second
    
    def _collect_metrics(self) -> SystemMetrics:
        """Collect current system metrics."""
        metrics = SystemMetrics(timestamp=datetime.now())
        
        # CPU usage
        metrics.cpu_percent = self._get_cpu_percent()
        
        # Memory usage
        metrics.memory_percent = self._get_memory_percent()
        
        # GPU metrics (if available)
        gpu_metrics = self._get_gpu_metrics()
        metrics.gpu_percent = gpu_metrics.get('utilization', 0.0)
        metrics.gpu_memory_percent = gpu_metrics.get('memory', 0.0)
        metrics.gpu_temperature_c = gpu_metrics.get('temperature', 0.0)
        
        return metrics
    
    def _get_cpu_percent(self) -> float:
        """Get CPU usage percentage."""
        try:
            if platform.system() == "Windows":
                result = subprocess.run(
                    ["wmic", "cpu", "get", "LoadPercentage", "/format:csv"],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0:
                    lines = [l.strip() for l in result.stdout.strip().split('\n') if l.strip()]
                    if len(lines) >= 2:
                        parts = lines[-1].split(',')
                        if len(parts) >= 2 and parts[1].isdigit():
                            return float(parts[1])
                            
            elif platform.system() == "Linux":
                with open("/proc/stat") as f:
                    line = f.readline()
                    parts = line.split()
                    if len(parts) >= 5:
                        idle = int(parts[4])
                        total = sum(int(p) for p in parts[1:])
                        return 100.0 * (1 - idle / total) if total > 0 else 0.0
                        
            elif platform.system() == "Darwin":
                result = subprocess.run(
                    ["top", "-l", "1", "-n", "0"],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0:
                    for line in result.stdout.split('\n'):
                        if 'CPU usage' in line:
                            # Parse "CPU usage: X% user, Y% sys, Z% idle"
                            match = __import__('re').search(r'(\d+\.?\d*)% idle', line)
                            if match:
                                return 100.0 - float(match.group(1))
        except Exception:
            pass
        return 0.0
    
    def _get_memory_percent(self) -> float:
        """Get memory usage percentage."""
        try:
            if platform.system() == "Windows":
                result = subprocess.run(
                    ["wmic", "OS", "get", "FreePhysicalMemory,TotalVisibleMemorySize", "/format:csv"],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0:
                    lines = [l.strip() for l in result.stdout.strip().split('\n') if l.strip()]
                    if len(lines) >= 2:
                        parts = lines[-1].split(',')
                        if len(parts) >= 3:
                            free = int(parts[1]) if parts[1].isdigit() else 0
                            total = int(parts[2]) if parts[2].isdigit() else 1
                            return 100.0 * (1 - free / total) if total > 0 else 0.0
                            
            elif platform.system() == "Linux":
                with open("/proc/meminfo") as f:
                    content = f.read()
                    total = available = 0
                    for line in content.split('\n'):
                        if line.startswith("MemTotal:"):
                            total = int(line.split()[1])
                        elif line.startswith("MemAvailable:"):
                            available = int(line.split()[1])
                    if total > 0:
                        return 100.0 * (1 - available / total)
                        
            elif platform.system() == "Darwin":
                # Get memory pressure from vm_stat
                result = subprocess.run(
                    ["vm_stat"],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0:
                    # Parse vm_stat output
                    stats = {}
                    for line in result.stdout.split('\n'):
                        if ':' in line:
                            key, val = line.split(':')
                            # Remove " pages" and periods, convert to int
                            val = val.strip().rstrip('.').replace(' pages', '')
                            if val.isdigit():
                                stats[key.strip()] = int(val)
                    
                    # Calculate memory usage (page size is typically 4096 bytes on macOS)
                    page_size = 4096
                    free = stats.get('Pages free', 0)
                    inactive = stats.get('Pages inactive', 0)
                    speculative = stats.get('Pages speculative', 0)
                    wired = stats.get('Pages wired down', 0)
                    active = stats.get('Pages active', 0)
                    compressed = stats.get('Pages occupied by compressor', 0)
                    
                    # Available = free + inactive + speculative
                    available = (free + inactive + speculative) * page_size
                    # Used = wired + active + compressed
                    used = (wired + active + compressed) * page_size
                    total = available + used
                    
                    if total > 0:
                        return 100.0 * used / total
        except Exception:
            pass
        return 0.0
    
    def _get_gpu_metrics(self) -> Dict[str, float]:
        """Get GPU utilization, memory, and temperature."""
        metrics = {'utilization': 0.0, 'memory': 0.0, 'temperature': 0.0}
        
        # Try NVIDIA first (most common for transcoding)
        if self._try_nvidia_metrics(metrics):
            return metrics
        
        # Try AMD
        if self._try_amd_metrics(metrics):
            return metrics
        
        # Try Intel (integrated)
        if self._try_intel_metrics(metrics):
            return metrics
        
        return metrics
    
    def _try_nvidia_metrics(self, metrics: Dict[str, float]) -> bool:
        """Try to get NVIDIA GPU metrics."""
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                parts = result.stdout.strip().split(', ')
                if len(parts) >= 4:
                    metrics['utilization'] = float(parts[0])
                    mem_used = float(parts[1])
                    mem_total = float(parts[2])
                    metrics['memory'] = 100.0 * mem_used / mem_total if mem_total > 0 else 0.0
                    metrics['temperature'] = float(parts[3])
                    return True
        except Exception:
            pass
        return False
    
    def _try_amd_metrics(self, metrics: Dict[str, float]) -> bool:
        """Try to get AMD GPU metrics."""
        try:
            if platform.system() == "Windows":
                # AMD on Windows - try AMD ADL or rocm-smi
                result = subprocess.run(
                    ["rocm-smi", "--showuse", "--showtemp", "--showmeminfo", "vram", "--csv"],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0 and result.stdout.strip():
                    lines = result.stdout.strip().split('\n')
                    for line in lines[1:]:  # Skip header
                        parts = line.split(',')
                        if len(parts) >= 4:
                            metrics['utilization'] = float(parts[1]) if parts[1] else 0.0
                            metrics['temperature'] = float(parts[2]) if parts[2] else 0.0
                            # Memory from vram
                            if len(parts) >= 6:
                                used = float(parts[4]) if parts[4] else 0
                                total = float(parts[5]) if parts[5] else 1
                                metrics['memory'] = 100.0 * used / total if total > 0 else 0.0
                            return True
                            
            elif platform.system() == "Linux":
                # AMD on Linux - use rocm-smi or sysfs
                result = subprocess.run(
                    ["rocm-smi", "-u", "-t", "--showmeminfo", "vram", "--csv"],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0:
                    lines = result.stdout.strip().split('\n')
                    for line in lines[1:]:
                        parts = line.split(',')
                        if len(parts) >= 3:
                            metrics['utilization'] = float(parts[1]) if parts[1] else 0.0
                            metrics['temperature'] = float(parts[2]) if parts[2] else 0.0
                            return True
                
                # Fallback: try sysfs for AMD
                gpu_busy_path = "/sys/class/drm/card0/device/gpu_busy_percent"
                temp_path = "/sys/class/drm/card0/device/hwmon/hwmon0/temp1_input"
                
                if os.path.exists(gpu_busy_path):
                    with open(gpu_busy_path) as f:
                        metrics['utilization'] = float(f.read().strip())
                    
                if os.path.exists(temp_path):
                    with open(temp_path) as f:
                        # Temperature in millidegrees
                        metrics['temperature'] = float(f.read().strip()) / 1000.0
                    
                if metrics['utilization'] > 0 or metrics['temperature'] > 0:
                    return True
                    
        except Exception:
            pass
        return False
    
    def _try_intel_metrics(self, metrics: Dict[str, float]) -> bool:
        """Try to get Intel GPU metrics."""
        try:
            if platform.system() == "Linux":
                # Intel on Linux - try intel_gpu_top or sysfs
                # intel_gpu_top requires root, so try sysfs first
                freq_path = "/sys/class/drm/card0/gt_cur_freq_mhz"
                max_freq_path = "/sys/class/drm/card0/gt_max_freq_mhz"
                
                if os.path.exists(freq_path) and os.path.exists(max_freq_path):
                    with open(freq_path) as f:
                        cur_freq = float(f.read().strip())
                    with open(max_freq_path) as f:
                        max_freq = float(f.read().strip())
                    
                    # Estimate utilization from frequency scaling
                    if max_freq > 0:
                        metrics['utilization'] = 100.0 * cur_freq / max_freq
                        return True
                        
            elif platform.system() == "Windows":
                # Intel on Windows - use PowerShell to query performance counters
                result = subprocess.run(
                    ["powershell", "-Command",
                     "(Get-Counter '\\GPU Engine(*engtype_3D)\\Utilization Percentage').CounterSamples | "
                     "Select-Object -First 1 -ExpandProperty CookedValue"],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0 and result.stdout.strip():
                    try:
                        metrics['utilization'] = float(result.stdout.strip())
                        return True
                    except ValueError:
                        pass
                        
        except Exception:
            pass
        return False
    
    @property
    def current(self) -> SystemMetrics:
        """Get current metrics."""
        with self._lock:
            return self._metrics
    
    def get_average_load(self, seconds: int = 30) -> float:
        """Get average load factor over last N seconds."""
        with self._lock:
            if not self._history:
                return 0.0
            
            samples = self._history[-seconds:] if len(self._history) >= seconds else self._history
            return sum(m.load_factor for m in samples) / len(samples)
    
    def get_trend(self) -> str:
        """Get load trend: 'increasing', 'stable', or 'decreasing'."""
        with self._lock:
            if len(self._history) < 10:
                return "stable"
            
            recent = sum(m.load_factor for m in self._history[-5:]) / 5
            older = sum(m.load_factor for m in self._history[-10:-5]) / 5
            
            diff = recent - older
            if diff > 0.1:
                return "increasing"
            elif diff < -0.1:
                return "decreasing"
            return "stable"


@dataclass
class TranscodeJob:
    """Represents a transcoding job for queue management."""
    job_id: str
    source: str
    priority: int = 5  # 1=highest, 10=lowest
    created_at: datetime = field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    estimated_duration_s: float = 0.0
    source_resolution: Tuple[int, int] = (1920, 1080)
    target_resolution: Tuple[int, int] = (1920, 1080)
    is_hdr: bool = False
    user_id: Optional[str] = None
    
    @property
    def complexity_score(self) -> float:
        """Estimate transcoding complexity (1.0 = baseline 1080p)."""
        base = (self.source_resolution[0] * self.source_resolution[1]) / (1920 * 1080)
        hdr_multiplier = 1.5 if self.is_hdr else 1.0
        return base * hdr_multiplier
    
    @property
    def wait_time_s(self) -> float:
        """Time spent waiting in queue."""
        return (datetime.now() - self.created_at).total_seconds()


class LoadBalancer:
    """
    Enterprise load balancer for transcoding jobs.
    Manages queue, adjusts quality based on load, and prevents overload.
    """
    
    def __init__(self, profile: SystemProfile, monitor: SystemMonitor):
        self.profile = profile
        self.monitor = monitor
        self._active_jobs: Dict[str, TranscodeJob] = {}
        self._queue: List[TranscodeJob] = []
        self._lock = threading.Lock()
        self._job_history: List[Dict[str, Any]] = []
        self._max_history = 1000
        
        # Dynamic limits
        self._base_max_jobs = profile.max_concurrent_jobs
        self._current_max_jobs = self._base_max_jobs
        
        # Quality reduction thresholds
        self._quality_reduction_factor = 1.0  # 1.0 = full quality, 0.5 = half
    
    @property
    def active_job_count(self) -> int:
        """Get number of active transcoding jobs."""
        with self._lock:
            return len(self._active_jobs)
    
    @property
    def queue_length(self) -> int:
        """Get number of queued jobs."""
        with self._lock:
            return len(self._queue)
    
    @property
    def current_max_jobs(self) -> int:
        """Get current dynamic max jobs limit."""
        return self._current_max_jobs
    
    @property
    def quality_factor(self) -> float:
        """Get current quality reduction factor."""
        return self._quality_reduction_factor
    
    def can_accept_job(self, job: TranscodeJob) -> Tuple[bool, str]:
        """Check if a new job can be accepted."""
        metrics = self.monitor.current
        
        # Check if system is overloaded
        if metrics.is_overloaded:
            return False, "System overloaded - GPU temperature or resource usage too high"
        
        # Check thermal throttling
        if metrics.gpu_temperature_c > 80:
            return False, f"GPU thermal throttling ({metrics.gpu_temperature_c}°C)"
        
        # Check if at capacity
        if self.active_job_count >= self._current_max_jobs:
            # Can queue but not start immediately
            return True, f"Queued (position {self.queue_length + 1})"
        
        return True, "Ready to start"
    
    def add_job(self, job: TranscodeJob) -> Tuple[bool, str]:
        """Add a job to the queue or start it immediately."""
        can_accept, reason = self.can_accept_job(job)
        
        if not can_accept:
            return False, reason
        
        with self._lock:
            if len(self._active_jobs) < self._current_max_jobs:
                # Start immediately
                job.started_at = datetime.now()
                self._active_jobs[job.job_id] = job
                logger.info(f"[LoadBalancer] Started job {job.job_id} (active: {len(self._active_jobs)})")
                return True, "Started"
            else:
                # Add to queue
                self._queue.append(job)
                self._queue.sort(key=lambda j: (j.priority, j.created_at))
                logger.info(f"[LoadBalancer] Queued job {job.job_id} (position: {len(self._queue)})")
                return True, f"Queued (position {len(self._queue)})"
    
    def complete_job(self, job_id: str, success: bool = True):
        """Mark a job as complete and potentially start next queued job."""
        with self._lock:
            if job_id in self._active_jobs:
                job = self._active_jobs.pop(job_id)
                
                # Record history
                self._job_history.append({
                    'job_id': job_id,
                    'success': success,
                    'duration_s': (datetime.now() - job.started_at).total_seconds() if job.started_at else 0,
                    'wait_time_s': job.wait_time_s,
                    'complexity': job.complexity_score,
                    'timestamp': datetime.now().isoformat(),
                })
                if len(self._job_history) > self._max_history:
                    self._job_history.pop(0)
                
                logger.info(f"[LoadBalancer] Completed job {job_id} (success={success})")
            
            # Start next queued job if available
            self._process_queue()
    
    def _process_queue(self):
        """Process queue and start jobs if capacity available."""
        while self._queue and len(self._active_jobs) < self._current_max_jobs:
            job = self._queue.pop(0)
            job.started_at = datetime.now()
            self._active_jobs[job.job_id] = job
            logger.info(f"[LoadBalancer] Started queued job {job.job_id}")
    
    def update_limits(self):
        """Dynamically update limits based on current load."""
        metrics = self.monitor.current
        load = metrics.load_factor
        trend = self.monitor.get_trend()
        
        # Adjust max concurrent jobs
        if metrics.gpu_temperature_c > 80 or load > 0.9:
            # Reduce capacity
            self._current_max_jobs = max(1, self._base_max_jobs - 1)
            self._quality_reduction_factor = 0.7
        elif metrics.gpu_temperature_c > 70 or load > 0.75:
            # Moderate load
            self._current_max_jobs = self._base_max_jobs
            self._quality_reduction_factor = 0.85
        elif load < 0.5 and trend == "decreasing":
            # System is underutilized, can handle more
            self._current_max_jobs = min(self._base_max_jobs + 1, 6)
            self._quality_reduction_factor = 1.0
        else:
            # Normal operation
            self._current_max_jobs = self._base_max_jobs
            self._quality_reduction_factor = 1.0
    
    def get_adjusted_preset(self, preset: QualityPreset) -> QualityPreset:
        """Get quality-adjusted preset based on current load."""
        if self._quality_reduction_factor >= 1.0:
            return preset
        
        # Reduce bitrate based on load
        original_bitrate = self._parse_bitrate(preset.video_bitrate)
        adjusted_bitrate = original_bitrate * self._quality_reduction_factor
        
        return QualityPreset(
            name=f"{preset.name}-adaptive",
            width=preset.width,
            height=preset.height,
            video_bitrate=f"{adjusted_bitrate:.1f}M",
            audio_bitrate=preset.audio_bitrate,
            crf=preset.crf + int((1 - self._quality_reduction_factor) * 6),  # Increase CRF
            hw_preset=preset.hw_preset
        )
    
    def _parse_bitrate(self, bitrate_str: str) -> float:
        """Parse bitrate string to Mbps."""
        bitrate_str = bitrate_str.upper().strip()
        if bitrate_str.endswith('M'):
            return float(bitrate_str[:-1])
        elif bitrate_str.endswith('K'):
            return float(bitrate_str[:-1]) / 1000
        return float(bitrate_str) / 1_000_000
    
    def get_stats(self) -> Dict[str, Any]:
        """Get comprehensive load balancer statistics."""
        with self._lock:
            recent_jobs = self._job_history[-100:] if self._job_history else []
            successful = sum(1 for j in recent_jobs if j.get('success', False))
            avg_duration = sum(j.get('duration_s', 0) for j in recent_jobs) / len(recent_jobs) if recent_jobs else 0
            avg_wait = sum(j.get('wait_time_s', 0) for j in recent_jobs) / len(recent_jobs) if recent_jobs else 0
            
            return {
                'active_jobs': len(self._active_jobs),
                'queued_jobs': len(self._queue),
                'max_concurrent_jobs': self._current_max_jobs,
                'base_max_jobs': self._base_max_jobs,
                'quality_factor': self._quality_reduction_factor,
                'system_load': self.monitor.current.load_factor,
                'gpu_temperature': self.monitor.current.gpu_temperature_c,
                'gpu_utilization': self.monitor.current.gpu_percent,
                'cpu_utilization': self.monitor.current.cpu_percent,
                'memory_utilization': self.monitor.current.memory_percent,
                'load_trend': self.monitor.get_trend(),
                'recent_success_rate': successful / len(recent_jobs) if recent_jobs else 1.0,
                'avg_job_duration_s': avg_duration,
                'avg_wait_time_s': avg_wait,
                'total_jobs_processed': len(self._job_history),
            }


class AdaptiveTranscodeManager:
    """
    Enterprise-grade adaptive transcoding manager.
    Combines hardware profiling, real-time monitoring, load balancing,
    and dynamic quality adjustment for optimal transcoding performance.
    """
    
    def __init__(self, capabilities: Capabilities):
        self.capabilities = capabilities
        
        # Initialize components
        self.profiler = HardwareProfiler(capabilities)
        self.profile = self.profiler.get_profile()
        self.monitor = SystemMonitor()
        self.load_balancer = LoadBalancer(self.profile, self.monitor)
        self.quality_selector = AdaptiveQualitySelector(self.profile)
        
        # Start monitoring
        self.monitor.start()
        
        # Background update thread
        self._running = True
        self._update_thread = threading.Thread(target=self._update_loop, daemon=True)
        self._update_thread.start()
        
        logger.info(f"[AdaptiveManager] Initialized - Tier: {self.profile.tier.value}, "
                   f"Max Jobs: {self.profile.max_concurrent_jobs}")
    
    def _update_loop(self):
        """Background loop to update dynamic limits."""
        while self._running:
            try:
                self.load_balancer.update_limits()
                
                # Update power source (laptop might plug/unplug)
                new_power = self.profiler._detect_power_source()
                if new_power != self.profile.power_source:
                    logger.info(f"[AdaptiveManager] Power source changed: {new_power.value}")
                    self.profile.power_source = new_power
                    # Recalculate tier
                    self.profile.tier = self.profiler._calculate_tier(self.profile)
                    self.profile.max_concurrent_jobs = self.profiler._get_max_jobs(self.profile)
                    self.load_balancer._base_max_jobs = self.profile.max_concurrent_jobs
                    
            except Exception as e:
                logger.debug(f"[AdaptiveManager] Update error: {e}")
            
            time.sleep(5.0)  # Update every 5 seconds
    
    def shutdown(self):
        """Shutdown the manager and cleanup resources."""
        self._running = False
        self.monitor.stop()
        if self._update_thread:
            self._update_thread.join(timeout=2.0)
        logger.info("[AdaptiveManager] Shutdown complete")
    
    def get_optimal_settings(self, media_info: MediaInfo) -> Dict[str, Any]:
        """
        Get optimal transcoding settings for given media.
        Considers hardware limits, current load, and source characteristics.
        """
        # Get base preset from hardware limits
        preset = self.quality_selector.get_single_best_preset(media_info)
        
        # Adjust for current load
        adjusted_preset = self.load_balancer.get_adjusted_preset(preset)
        
        # Determine if transcoding is needed
        needs_transcode, reason = self.quality_selector.should_transcode(media_info)
        
        return {
            'needs_transcode': needs_transcode,
            'reason': reason,
            'preset': adjusted_preset,
            'encoder': self.profile.recommended_encoder,
            'max_concurrent_jobs': self.load_balancer.current_max_jobs,
            'current_load': self.monitor.current.load_factor,
            'quality_factor': self.load_balancer.quality_factor,
            'estimated_wait_s': self._estimate_wait_time(),
        }
    
    def _estimate_wait_time(self) -> float:
        """Estimate wait time for a new job."""
        stats = self.load_balancer.get_stats()
        queued = stats['queued_jobs']
        avg_duration = stats['avg_job_duration_s']
        max_jobs = stats['max_concurrent_jobs']
        
        if queued == 0 and stats['active_jobs'] < max_jobs:
            return 0.0
        
        # Estimate based on queue position and average duration
        return (queued / max_jobs) * avg_duration if max_jobs > 0 else 0.0
    
    def submit_job(self, job: TranscodeJob) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Submit a transcoding job to the manager.
        Returns (accepted, message, details).
        """
        accepted, message = self.load_balancer.add_job(job)
        
        details = {
            'job_id': job.job_id,
            'accepted': accepted,
            'message': message,
            'queue_position': self.load_balancer.queue_length if 'Queued' in message else 0,
            'estimated_wait_s': self._estimate_wait_time(),
            'current_stats': self.load_balancer.get_stats(),
        }
        
        return accepted, message, details
    
    def complete_job(self, job_id: str, success: bool = True):
        """Mark a job as complete."""
        self.load_balancer.complete_job(job_id, success)
    
    def get_status(self) -> Dict[str, Any]:
        """Get comprehensive system status."""
        metrics = self.monitor.current
        stats = self.load_balancer.get_stats()
        
        return {
            'hardware': {
                'tier': self.profile.tier.value,
                'gpu_name': self.profile.gpu_name,
                'gpu_vram_mb': self.profile.gpu_vram_mb,
                'is_laptop': self.profile.is_laptop,
                'power_source': self.profile.power_source.value,
                'max_resolution': self.profile.max_resolution,
                'max_bitrate_mbps': self.profile.max_bitrate_mbps,
                'recommended_encoder': self.profile.recommended_encoder,
            },
            'realtime': {
                'cpu_percent': metrics.cpu_percent,
                'memory_percent': metrics.memory_percent,
                'gpu_percent': metrics.gpu_percent,
                'gpu_memory_percent': metrics.gpu_memory_percent,
                'gpu_temperature_c': metrics.gpu_temperature_c,
                'load_factor': metrics.load_factor,
                'is_overloaded': metrics.is_overloaded,
                'load_trend': self.monitor.get_trend(),
            },
            'jobs': stats,
            'timestamp': datetime.now().isoformat(),
        }


# Global instances
_profiler: Optional[HardwareProfiler] = None
_manager: Optional[AdaptiveTranscodeManager] = None


def get_hardware_profiler(capabilities: Capabilities) -> HardwareProfiler:
    """Get or create hardware profiler."""
    global _profiler
    if _profiler is None:
        _profiler = HardwareProfiler(capabilities)
    return _profiler


def get_adaptive_quality_selector(capabilities: Capabilities) -> AdaptiveQualitySelector:
    """Get adaptive quality selector for current hardware."""
    profiler = get_hardware_profiler(capabilities)
    profile = profiler.get_profile()
    return AdaptiveQualitySelector(profile)


def get_adaptive_manager(capabilities: Capabilities) -> AdaptiveTranscodeManager:
    """Get or create the enterprise adaptive transcode manager."""
    global _manager
    if _manager is None:
        _manager = AdaptiveTranscodeManager(capabilities)
    return _manager

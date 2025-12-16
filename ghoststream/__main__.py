"""
Main entry point for GhostStream
"""

import argparse
import socket
import sys
import uvicorn

from . import __version__
from .config import load_config, set_config
from .logging_config import setup_logging


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="GhostStream - Open Source Transcoding Service",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m ghoststream                    # Start with default config
  python -m ghoststream -c config.yaml     # Start with custom config
  python -m ghoststream --port 9000        # Start on different port
  python -m ghoststream --detect-hw        # Detect hardware and exit
        """
    )
    
    parser.add_argument(
        "-v", "--version",
        action="version",
        version=f"GhostStream v{__version__}"
    )
    
    parser.add_argument(
        "-c", "--config",
        type=str,
        default=None,
        help="Path to configuration file"
    )
    
    parser.add_argument(
        "--host",
        type=str,
        default=None,
        help="Host to bind to (overrides config)"
    )
    
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port to bind to (overrides config)"
    )
    
    parser.add_argument(
        "--log-level",
        type=str,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default=None,
        help="Logging level (overrides config)"
    )
    
    parser.add_argument(
        "--detect-hw",
        action="store_true",
        help="Detect hardware capabilities and exit"
    )
    
    parser.add_argument(
        "--no-mdns",
        action="store_true",
        help="Disable mDNS service advertisement"
    )
    
    args = parser.parse_args()
    
    # Load configuration
    config = load_config(args.config)
    
    # Apply command-line overrides
    if args.host:
        config.server.host = args.host
    if args.port:
        config.server.port = args.port
    if args.log_level:
        config.logging.level = args.log_level
    if args.no_mdns:
        config.mdns.enabled = False
    
    set_config(config)
    
    # Setup logging
    setup_logging()
    
    # Hardware detection mode
    if args.detect_hw:
        detect_hardware()
        return
    
    # Get local IP address
    local_ip = _get_local_ip(config.server.host)
    
    # Print professional startup banner
    _print_startup_banner(config, local_ip)
    
    # Uvicorn configuration - use uvloop on Linux for better async performance
    uvicorn.run(
        "ghoststream.api:app",
        host=config.server.host,
        port=config.server.port,
        log_level=config.logging.level.lower(),
        access_log=config.logging.level == "DEBUG",
        loop="uvloop" if sys.platform != "win32" else "asyncio",
        timeout_keep_alive=30,
    )


def _get_local_ip(configured_host: str) -> str:
    """Get the local IP address for display."""
    if configured_host != "0.0.0.0":
        return configured_host
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _print_startup_banner(config, local_ip: str):
    """Print a professional startup banner with key information."""
    from .hardware import HardwareDetector
    
    # Detect hardware for display
    try:
        detector = HardwareDetector(config.transcoding.ffmpeg_path)
        capabilities = detector.detect_all(config.transcoding.max_concurrent_jobs)
        hw_accel = capabilities.get_best_hw_accel().value.upper()
        ffmpeg_ver = capabilities.ffmpeg_version or "Unknown"
    except Exception:
        hw_accel = "NONE"
        ffmpeg_ver = "Unknown"
    
    width = 62
    
    print("\n" + "═" * width)
    print("   _____ _               _   _____ _                          ")
    print("  / ____| |             | | / ____| |                         ")
    print(" | |  __| |__   ___  ___| || (___ | |_ _ __ ___  __ _ _ __ ___ ")
    print(" | | |_ | '_ \\ / _ \\/ __| __\\___ \\| __| '__/ _ \\/ _` | '_ ` _ \\")
    print(" | |__| | | | | (_) \\__ \\ |_ ___) | |_| | |  __/ (_| | | | | | |")
    print("  \\_____|_| |_|\\___/|___/\\__|____/ \\__|_|  \\___|\\__,_|_| |_| |_|")
    print("═" * width)
    print()
    print(f"  {'SERVICE INFO':-^{width-4}}")
    print(f"  Version:          {__version__}")
    print(f"  FFmpeg:           {ffmpeg_ver}")
    print(f"  HW Acceleration:  {hw_accel}")
    print()
    print(f"  {'NETWORK':-^{width-4}}")
    print(f"  Local URL:        http://{local_ip}:{config.server.port}")
    print(f"  Bind Address:     {config.server.host}:{config.server.port}")
    print(f"  mDNS Discovery:   {'Enabled' if config.mdns.enabled else 'Disabled'}")
    print()
    print(f"  {'CONFIGURATION':-^{width-4}}")
    print(f"  Max Jobs:         {config.transcoding.max_concurrent_jobs}")
    print(f"  Temp Directory:   {config.transcoding.temp_directory}")
    print(f"  Log Level:        {config.logging.level}")
    print()
    print("═" * width)
    print(f"  Server starting... Logs will appear below.")
    print("═" * width)
    print()


def detect_hardware():
    """Detect and print hardware capabilities."""
    from .hardware import HardwareDetector
    from .config import get_config
    
    config = get_config()
    
    print("\n=== GhostStream Hardware Detection ===\n")
    
    try:
        detector = HardwareDetector(config.transcoding.ffmpeg_path)
        capabilities = detector.detect_all(config.transcoding.max_concurrent_jobs)
        
        print(f"Platform: {capabilities.platform}")
        print(f"FFmpeg Version: {capabilities.ffmpeg_version}")
        print()
        
        print("Hardware Acceleration:")
        print("-" * 40)
        
        for hw in capabilities.hw_accels:
            status = "[OK] Available" if hw.available else "[--] Not available"
            print(f"  {hw.type.value.upper():15} {status}")
            
            if hw.available and hw.encoders:
                print(f"    Encoders: {', '.join(hw.encoders[:5])}")
            
            if hw.gpu_info:
                print(f"    GPU: {hw.gpu_info.name}")
                print(f"    Memory: {hw.gpu_info.memory_mb} MB")
        
        print()
        print("Supported Video Codecs:")
        print(f"  {', '.join(capabilities.video_codecs)}")
        
        print()
        print("Supported Audio Codecs:")
        print(f"  {', '.join(capabilities.audio_codecs)}")
        
        print()
        print(f"Best Hardware Acceleration: {capabilities.get_best_hw_accel().value}")
        
    except Exception as e:
        print(f"Error detecting hardware: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

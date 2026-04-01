import argparse
import logging
import signal
import socket
import sys
import os

# DO NOT patch here - it poisons the TUI.
# ONLY patch inside the server-only runtime path.

from . import __version__
from .config import load_config, set_config, get_config
from .logging_config import setup_logging
from .runtime import create_runtime

logger = logging.getLogger(__name__)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="GhostStream - Open Source Transcoding Service",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument("-v", "--version", action="version", version=f"GhostStream v{__version__}")
    parser.add_argument("-c", "--config", type=str, default=None, help="Path to configuration file")
    parser.add_argument("--host", type=str, default=None, help="Host to bind to")
    parser.add_argument("--port", type=int, default=None, help="Port to bind to")
    parser.add_argument("--log-level", type=str, choices=["DEBUG", "INFO", "WARNING", "ERROR"], default=None)
    parser.add_argument("--log-format", type=str, choices=["json", "text"], default=None)
    parser.add_argument("--detect-hw", action="store_true", help="Detect hardware capabilities and exit")
    parser.add_argument("--no-mdns", action="store_true", help="Disable mDNS")
    parser.add_argument("--server-only", action="store_true", help="Run the core engine without TUI dashboard")

    args = parser.parse_args()

    # Load configuration
    config = load_config(args.config)

    # Apply command-line overrides
    if args.host: config.server.host = args.host
    if args.port: config.server.port = args.port
    if args.log_level: config.logging.level = args.log_level
    if args.log_format: config.logging.format = args.log_format
    if args.no_mdns: config.mdns.enabled = False

    set_config(config)

    # Hardware detection mode
    if args.detect_hw:
        import gevent.monkey
        gevent.monkey.patch_all()
        setup_logging()
        detect_hardware()
        return
        
    # ENGINE ONLY MODE (Used internally by TUI or for headless operation)
    if args.server_only:
        import gevent.monkey
        gevent.monkey.patch_all()
        
        # FORCE UNBUFFERED OUTPUT FOR TUI COMPATIBILITY
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(line_buffering=True)
            sys.stderr.reconfigure(line_buffering=True)
            
        setup_logging()
        
        runtime = create_runtime()
        runtime.start()

        def _shutdown(*_args):
            runtime.stop()
            sys.exit(0)

        signal.signal(signal.SIGTERM, _shutdown)
        signal.signal(signal.SIGINT, _shutdown)
        
        # Keep process alive with gevent
        import gevent
        try:
            while True:
                gevent.sleep(1.0)
        except KeyboardInterrupt:
            runtime.stop()
            sys.exit(0)
        return

    # TUI MANAGEMENT MODE (DEFAULTS)
    setup_logging(console_output=True)

    # --- PRE-FLIGHT CHECK ---
    from .hardware import HardwareDetector
    try:
        HardwareDetector()
    except RuntimeError:
        logger.error("%s", "!" * 50)
        logger.error("FFMPEG NOT FOUND")
        logger.error("GhostStream requires FFmpeg to transcode video.")
        logger.error("Please install it for your system:")
        if sys.platform == "darwin":
            logger.error("  macOS: brew install ffmpeg")
        elif sys.platform == "win32":
            logger.error("  Windows: download from https://www.gyan.dev/ffmpeg/builds/")
        else:
            logger.error("  Linux: sudo apt update && sudo apt install ffmpeg")
        logger.error("After installing, restart GhostStream.")
        logger.error("%s", "!" * 50)
        sys.exit(1)

    logger.info("[BOOT] GhostStream v%s starting...", __version__)
    
    # Unified Mode requires monkey patching the main process so the 
    # internal engine thread works correctly.
    import gevent.monkey
    gevent.monkey.patch_all()
    
    logger.info("[BOOT] Launching Management Dashboard...")
    
    # We turn off console logging AFTER the UI is starting to avoid swallowing errors
    setup_logging(console_output=False)
    
    logger.info("[BOOT] Importing TUI...")
    from .tui import run_tui_app
    logger.info("[BOOT] TUI imported. Starting app...")
    try:
        run_tui_app(
            host=config.server.host if config.server.host != "0.0.0.0" else "127.0.0.1",
            port=config.server.port,
            config_path=args.config
        )
    except KeyboardInterrupt:
        pass


def _get_local_ip(configured_host: str) -> str:
    if configured_host != "0.0.0.0": return configured_host
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception: return "127.0.0.1"


def detect_hardware():
    from .hardware import HardwareDetector
    from .config import get_config
    config = get_config()
    logger.info("=== GhostStream Hardware Detection ===")
    try:
        detector = HardwareDetector(config.transcoding.ffmpeg_path)
        capabilities = detector.detect_all(config.transcoding.max_concurrent_jobs)
        logger.info("Platform: %s", capabilities.platform)
        logger.info("FFmpeg Version: %s", capabilities.ffmpeg_version)
        logger.info("Hardware Acceleration:")
        for hw in capabilities.hw_accels:
            status = "[OK] Available" if hw.available else "[--] Not available"
            logger.info("  %-15s %s", hw.type.value.upper(), status)
        logger.info("Best Hardware Acceleration: %s", capabilities.get_best_hw_accel().value)
    except Exception as e:
        logger.exception("Hardware detection failed: %s", e)
        sys.exit(1)

if __name__ == "__main__":
    main()

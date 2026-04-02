import argparse
import logging
import signal
import sys

from . import __version__

logger = logging.getLogger(__name__)

SERVER_RUNTIME_MODULES = {
    "flask",
    "geventwebsocket",
    "psutil",
    "pydantic_settings",
    "pythonjsonlogger",
    "rich",
    "textual",
    "yaml",
}


def _resolve_tui_hosts(configured_host: str) -> tuple[str, str]:
    bind_host = configured_host
    poll_host = "127.0.0.1" if configured_host == "0.0.0.0" else configured_host
    return poll_host, bind_host


def _raise_for_missing_server_dependency(exc: ModuleNotFoundError) -> None:
    missing = (getattr(exc, "name", None) or "").split(".", 1)[0]
    if missing not in SERVER_RUNTIME_MODULES:
        raise exc

    print(
        "GhostStream server runtime dependencies are not installed.\n"
        "Install them with:\n"
        "  pip install \"ghoststream[server]\"",
        file=sys.stderr,
    )
    raise SystemExit(1) from exc


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

    try:
        from .app.entrypoints import create_runtime
        from .config import load_config, set_config
        from .logging_config import setup_logging
    except ModuleNotFoundError as exc:
        _raise_for_missing_server_dependency(exc)

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
        
        runtime = create_runtime(config)
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
    from .tui.app import run_tui_app
    logger.info("[BOOT] TUI imported. Starting app...")
    try:
        poll_host, bind_host = _resolve_tui_hosts(config.server.host)
        run_tui_app(
            host=poll_host,
            port=config.server.port,
            config_path=args.config,
            bind_host=bind_host,
        )
    except KeyboardInterrupt:
        pass
def detect_hardware():
    try:
        from .config import get_config
        from .hardware import HardwareDetector
    except ModuleNotFoundError as exc:
        _raise_for_missing_server_dependency(exc)
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

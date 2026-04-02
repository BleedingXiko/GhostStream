"""
GhostStream Launcher - Entry point for PyInstaller builds.
Wraps the main function with error handling so users can see crash messages.
"""

import logging
import sys

from ghoststream.logging_config import setup_logging

logger = logging.getLogger(__name__)


def main():
    """Launch GhostStream with error handling for packaged builds."""
    setup_logging()
    try:
        from ghoststream.__main__ import main as ghoststream_main
        ghoststream_main()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    except Exception as e:
        logger.error("%s", "=" * 60)
        logger.error("GhostStream failed to start")
        logger.exception("%s: %s", type(e).__name__, e)
        logger.error("Common fixes:")
        logger.error("  1. Make sure FFmpeg is installed and in your PATH")
        logger.error("  2. Check if port 8765 is already in use")
        logger.error("  3. Try running from command line to see full output")
        logger.error("%s", "=" * 60)
        
        # Keep console open so user can read the error
        if sys.platform == "win32":
            input("\nPress Enter to exit...")
        else:
            logger.info("Press Ctrl+C to exit...")
            try:
                import time
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass
        sys.exit(1)


if __name__ == "__main__":
    main()

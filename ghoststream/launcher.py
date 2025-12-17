"""
GhostStream Launcher - Entry point for PyInstaller builds.
Wraps the main function with error handling so users can see crash messages.
"""

import sys
import traceback


def main():
    """Launch GhostStream with error handling for packaged builds."""
    try:
        from ghoststream.__main__ import main as ghoststream_main
        ghoststream_main()
    except KeyboardInterrupt:
        print("\nShutting down...")
    except Exception as e:
        print("\n" + "=" * 60)
        print("ERROR: GhostStream failed to start")
        print("=" * 60)
        print(f"\n{type(e).__name__}: {e}\n")
        traceback.print_exc()
        print("\n" + "=" * 60)
        print("Common fixes:")
        print("  1. Make sure FFmpeg is installed and in your PATH")
        print("  2. Check if port 8765 is already in use")
        print("  3. Try running from command line to see full output")
        print("=" * 60)
        
        # Keep console open so user can read the error
        if sys.platform == "win32":
            input("\nPress Enter to exit...")
        else:
            print("\nPress Ctrl+C to exit...")
            try:
                import time
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass
        sys.exit(1)


if __name__ == "__main__":
    main()

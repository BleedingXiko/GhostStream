#!/usr/bin/env python3
"""
GhostStream Demo - Zero-Config Showcase
========================================

Run this to see GhostStream in action with zero configuration.
Automatically detects server, transcodes a test video, and opens the player.

Usage:
    python examples/demo.py

What it does:
    1. Checks if GhostStream is running
    2. Starts transcoding a test video
    3. Shows real-time progress
    4. Opens VLC/browser when ready
    5. Cleans up on exit (Ctrl+C)
"""

import sys
import time
import signal
import subprocess
import platform
import webbrowser
from typing import Optional

try:
    from ghoststream import GhostStreamClient, TranscodeStatus
except ImportError:
    print("❌ Missing dependency: ghoststream SDK")
    print("   Install with: pip install ghoststream")
    print("   Or run from project root: pip install -e .")
    sys.exit(1)

# Configuration
GHOSTSTREAM_SERVER = "localhost:8765"
TEST_VIDEO = "https://test-videos.co.uk/vids/bigbuckbunny/mp4/h264/720/Big_Buck_Bunny_720_10s_1MB.mp4"
TEST_VIDEO_DURATION = "10 seconds"  # Note: This is a short demo clip

# Create SDK client
client = GhostStreamClient(manual_server=GHOSTSTREAM_SERVER)

# Global for cleanup
current_job_id: Optional[str] = None


def cleanup(sig=None, frame=None):
    """Clean up job on exit."""
    if current_job_id:
        print(f"\n🧹 Cleaning up job {current_job_id[:8]}...")
        try:
            client.delete_job_sync(current_job_id)
            print("✅ Cleaned up")
        except Exception:
            pass
    sys.exit(0)


def clear_line():
    """Clear current terminal line."""
    print("\r" + " " * 80 + "\r", end="", flush=True)


def print_banner():
    """Print welcome banner."""
    print()
    print("╔══════════════════════════════════════════════════════════════╗")
    print("║           👻 GhostStream Demo - Zero Config                  ║")
    print("╠══════════════════════════════════════════════════════════════╣")
    print("║  This demo will:                                             ║")
    print("║    1. Check server connection                                ║")
    print("║    2. Transcode a 10-second test clip (Big Buck Bunny)       ║")
    print("║    3. Show real-time progress                                ║")
    print("║    4. Open the stream in your player                         ║")
    print("║                                                              ║")
    print("║  Press Ctrl+C at any time to stop and clean up               ║")
    print("╚══════════════════════════════════════════════════════════════╝")
    print()


def check_server() -> bool:
    """Check if GhostStream is running."""
    print("🔍 Checking GhostStream server...", end=" ", flush=True)
    try:
        if not client.health_check_sync():
            print("❌ Failed")
            print()
            print("   GhostStream is not running!")
            print("   Start it with: python run.py")
            print()
            return False
        
        print(f"✅ Connected!")
        
        # Also get hardware info
        caps = client.get_capabilities_sync()
        if caps:
            print(f"   Version: {caps.get('version', 'unknown')}")
            hw_accels = [h['type'] for h in caps.get('hw_accels', []) if h.get('available')]
            if hw_accels:
                print(f"   Hardware: {', '.join(hw_accels).upper()}")
            else:
                print(f"   Hardware: Software (CPU)")
        
        return True
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


def start_transcode() -> Optional[str]:
    """Start a transcoding job."""
    global current_job_id
    
    print()
    print(f"🎬 Starting transcode...")
    print(f"   Source: Big Buck Bunny ({TEST_VIDEO_DURATION} demo clip)")
    
    try:
        job = client.transcode_sync(
            source=TEST_VIDEO,
            mode="stream",
            resolution="720p",
            video_codec="h264"
        )
        
        if job.status == TranscodeStatus.ERROR:
            print(f"❌ Failed: {job.error_message}")
            return None
        
        current_job_id = job.job_id
        print(f"   Job ID: {current_job_id[:8]}...")
        return current_job_id
        
    except Exception as e:
        print(f"❌ Error: {e}")
        return None


def wait_for_ready(job_id: str) -> Optional[str]:
    """Wait for transcode to be ready, showing progress."""
    print()
    print("⏳ Transcoding progress:")
    
    spinner = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    spin_idx = 0
    
    for attempt in range(120):  # 2 minute timeout
        try:
            job = client.get_job_status_sync(job_id)
            
            if not job:
                time.sleep(1)
                continue
            
            status = job.status.value
            progress = job.progress
            hw_accel = job.hw_accel_used or "pending"
            
            # Build progress bar
            bar_width = 30
            filled = int(bar_width * progress / 100)
            bar = "█" * filled + "░" * (bar_width - filled)
            
            # Update display
            clear_line()
            print(f"   {spinner[spin_idx]} [{bar}] {progress:5.1f}% | {status.upper()} | HW: {hw_accel}", end="", flush=True)
            spin_idx = (spin_idx + 1) % len(spinner)
            
            if job.status == TranscodeStatus.READY:
                clear_line()
                print(f"   ✅ [{bar}] 100.0% | READY | HW: {hw_accel}")
                return job.stream_url
            
            elif job.status == TranscodeStatus.ERROR:
                clear_line()
                print(f"   ❌ Error: {job.error_message or 'Unknown error'}")
                return None
            
            elif job.status == TranscodeStatus.CANCELLED:
                clear_line()
                print(f"   ⚠️ Job was cancelled")
                return None
            
            # For streaming, we can play as soon as we have a URL and it's processing
            if job.status == TranscodeStatus.PROCESSING and job.stream_url and progress > 5:
                clear_line()
                print(f"   ✅ [{bar}] {progress:5.1f}% | STREAMING | HW: {hw_accel}")
                return job.stream_url
            
            time.sleep(0.5)
            
        except Exception as e:
            time.sleep(1)
    
    print()
    print("   ⚠️ Timeout waiting for transcode")
    return None


def open_player(stream_url: str):
    """Open the stream in a media player."""
    print()
    print(f"📺 Stream URL:")
    print(f"   {stream_url}")
    print(f"   ℹ️  Note: This is a {TEST_VIDEO_DURATION} demo clip")
    print()
    
    system = platform.system()
    
    # Try to find VLC
    vlc_paths = {
        "Windows": [
            r"C:\Program Files\VideoLAN\VLC\vlc.exe",
            r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe",
        ],
        "Darwin": [  # macOS
            "/Applications/VLC.app/Contents/MacOS/VLC",
        ],
        "Linux": [
            "/usr/bin/vlc",
            "/snap/bin/vlc",
        ]
    }
    
    vlc_path = None
    for path in vlc_paths.get(system, []):
        try:
            if subprocess.run(["test", "-f", path] if system != "Windows" else ["cmd", "/c", f"if exist \"{path}\" echo yes"], 
                            capture_output=True, timeout=2).returncode == 0 or \
               (system == "Windows" and __import__("os").path.exists(path)):
                vlc_path = path
                break
        except Exception:
            pass
    
    # Check if VLC exists on Windows more directly
    if system == "Windows":
        import os
        for path in vlc_paths["Windows"]:
            if os.path.exists(path):
                vlc_path = path
                break
    
    print("🎮 Opening player...")
    
    if vlc_path:
        print(f"   Using VLC: {vlc_path}")
        try:
            if system == "Windows":
                subprocess.Popen([vlc_path, stream_url], 
                               creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0)
            else:
                subprocess.Popen([vlc_path, stream_url], 
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print("   ✅ VLC opened!")
            return True
        except Exception as e:
            print(f"   ⚠️ Failed to open VLC: {e}")
    
    # Fallback: open web player
    print("   VLC not found, opening web player...")
    
    # Create a simple HTML file to play HLS
    import tempfile
    import os
    
    html_content = f'''<!DOCTYPE html>
<html>
<head>
    <title>GhostStream Demo Player</title>
    <script src="https://cdn.jsdelivr.net/npm/hls.js@latest"></script>
    <style>
        body {{ margin: 0; background: #1a1a2e; display: flex; align-items: center; justify-content: center; min-height: 100vh; }}
        video {{ max-width: 90vw; max-height: 90vh; border-radius: 12px; }}
    </style>
</head>
<body>
    <video id="video" controls autoplay></video>
    <script>
        const video = document.getElementById('video');
        const src = '{stream_url}';
        if (Hls.isSupported()) {{
            const hls = new Hls();
            hls.loadSource(src);
            hls.attachMedia(video);
        }} else if (video.canPlayType('application/vnd.apple.mpegurl')) {{
            video.src = src;
        }}
    </script>
</body>
</html>'''
    
    # Write temp HTML file
    temp_dir = tempfile.gettempdir()
    html_path = os.path.join(temp_dir, "ghoststream_demo.html")
    with open(html_path, "w") as f:
        f.write(html_content)
    
    webbrowser.open(f"file://{html_path}")
    print("   ✅ Browser opened!")
    return True


def main():
    """Main demo flow."""
    # Setup signal handler for clean exit
    signal.signal(signal.SIGINT, cleanup)
    if hasattr(signal, 'SIGTERM'):
        signal.signal(signal.SIGTERM, cleanup)
    
    print_banner()
    
    # Step 1: Check server
    if not check_server():
        sys.exit(1)
    
    # Step 2: Start transcode
    job_id = start_transcode()
    if not job_id:
        sys.exit(1)
    
    # Step 3: Wait for ready
    stream_url = wait_for_ready(job_id)
    if not stream_url:
        cleanup()
        sys.exit(1)
    
    # Step 4: Open player
    open_player(stream_url)
    
    # Keep running until user exits
    print()
    print("═" * 60)
    print("✨ Demo running! Press Ctrl+C to stop and clean up.")
    print("═" * 60)
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        cleanup()


if __name__ == "__main__":
    main()

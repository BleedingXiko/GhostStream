"""
GhostStream SDK Examples
========================

These examples demonstrate how to use the GhostStream SDK for transcoding.

Installation:
    pip install ghoststream              # SDK only (lightweight)
    pip install ghoststream[server]      # Full server with all dependencies

Quick Start:
    from ghoststream import GhostStreamClient, TranscodeStatus
    
    client = GhostStreamClient(manual_server="localhost:8765")
    job = client.transcode_sync(source="http://...", resolution="720p")
    print(f"Stream: {job.stream_url}")

Examples:
    - minimal.py: Simplest usage in ~10 lines
    - quickstart.py: Interactive examples with menu
    - demo.py: Zero-config demo with progress display
    - ghosthub_integration.py: Full integration patterns for media servers

For more information, see the SDK documentation in ghoststream/client.py
"""

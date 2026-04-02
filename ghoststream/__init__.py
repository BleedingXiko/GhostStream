"""
GhostStream - Open Source Cross-Platform Transcoding Service

SDK Usage:
    from ghoststream import GhostStreamClient, TranscodeStatus
    
    # With mDNS auto-discovery
    client = GhostStreamClient()
    client.start_discovery()
    
    # Or with manual server
    client = GhostStreamClient(manual_server="192.168.4.2:8765")
    
    with client:
        job = client.transcode(source="http://...", resolution="1080p")
        print(f"Stream URL: {job.stream_url}")
"""

__version__ = "1.2.5"
__author__ = "GhostStream Contributors"

from ghoststream.client import (
    GhostStreamClient,
    GhostStreamServer,
    GhostStreamLoadBalancer,
    GhostStreamDiscoveryListener,
    TranscodeJob,
    TranscodeStatus,
    ClientConfig,
    LoadBalanceStrategy,
    ServerStats,
)

__all__ = [
    # Main client
    "GhostStreamClient",
    "GhostStreamServer",
    "GhostStreamLoadBalancer",
    "GhostStreamDiscoveryListener",
    # Data classes
    "TranscodeJob",
    "TranscodeStatus",
    "ClientConfig",
    "LoadBalanceStrategy",
    "ServerStats",
    # Version
    "__version__",
    "__author__",
]

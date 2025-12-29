"""
GhostHub registration for GhostStream
"""

import socket
import logging
import asyncio
import os
import uuid
from typing import Dict, Any, Tuple, Optional, List

from ..config import get_config
from ..hardware import get_capabilities

logger = logging.getLogger(__name__)

# Discovery configuration
DISCOVERY_URLS = [
    "http://ghosthub.local:5000",
    "http://ghosthub:5000",
    "http://10.0.0.1:5000",
    "http://192.168.1.1:5000",
    "http://192.168.0.1:5000",
    "http://192.168.4.1:5000",
]
DISCOVERY_TIMEOUT = 5.0  # Seconds per URL attempt


class GhostHubRegistration:
    """Handles push registration with GhostHub server."""
    
    def __init__(self, ghosthub_url: str, port: int = 8765):
        self.ghosthub_url = ghosthub_url.rstrip("/") if ghosthub_url else ""
        self.port = port
        self._stop_event = False
        self._registration_task = None
        self._discovered_url: Optional[str] = None  # Store successful discovery
    
    def _get_local_ip(self) -> str:
        """Get the local IP address."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    def _get_server_id(self) -> str:
        """
        Generate a stable unique server ID based on hostname and MAC address.
        This ensures the same server is recognized even if IP changes.
        """
        try:
            # Use hostname and MAC address to generate stable UUID
            hostname = socket.gethostname()
            mac = ':'.join(['{:02x}'.format((uuid.getnode() >> i) & 0xff)
                           for i in range(0, 8*6, 8)][::-1])
            # Generate UUID5 from hostname+MAC for stability
            server_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{hostname}-{mac}"))
            return server_id
        except Exception:
            # Fallback to random UUID (will be different each run)
            return str(uuid.uuid4())
    
    def _get_registration_payload(self) -> Dict[str, Any]:
        """Build registration payload with capabilities."""
        from .. import __version__
        
        capabilities = get_capabilities()
        hw_accels = [
            hw.type.value for hw in capabilities.hw_accels
            if hw.available
        ]
        
        local_ip = self._get_local_ip()

        return {
            "server_id": self._get_server_id(),  # Unique stable ID for deduplication
            "address": f"{local_ip}:{self.port}",
            "name": get_config().mdns.service_name,
            "version": __version__,
            "hw_accels": hw_accels,
            "video_codecs": capabilities.video_codecs,
            "audio_codecs": capabilities.audio_codecs,
            "max_jobs": capabilities.max_concurrent_jobs,
        }
    
    def register(self) -> bool:
        """Register this GhostStream instance with GhostHub."""
        import httpx
        
        # Allow override via environment variable
        ghosthub_url = os.environ.get('GHOSTHUB_URL', self.ghosthub_url)
        register_url = f"{ghosthub_url}/api/ghoststream/servers/register"
        
        try:
            payload = self._get_registration_payload()
            logger.info(f"[GhostHub] Registering at {register_url} with payload: {payload}")
            
            # Longer timeout for slow networks
            with httpx.Client(timeout=15.0) as client:
                resp = client.post(register_url, json=payload)
                
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("registered", False):
                        logger.info(f"[GhostHub] Registered successfully with GhostHub at {ghosthub_url}")
                        return True
                    else:
                        logger.warning(f"[GhostHub] Registration response: {data}")
                        return False
                else:
                    logger.warning(f"[GhostHub] Registration failed with status {resp.status_code}: {resp.text}")
                    return False
                    
        except Exception as e:
            if "ConnectError" in str(type(e)):
                logger.warning(f"[GhostHub] Cannot connect to {ghosthub_url} - is GhostHub running? Error: {e}")
            elif "TimeoutException" in str(type(e)):
                logger.warning(f"[GhostHub] Connection to {ghosthub_url} timed out after 15s")
            else:
                logger.warning(f"[GhostHub] Registration error: {e}")
            return False

    def _try_url(self, url: str) -> bool:
        """Test registration at a specific URL."""
        import httpx

        register_url = f"{url}/api/ghoststream/servers/register"

        try:
            payload = self._get_registration_payload()

            with httpx.Client(timeout=DISCOVERY_TIMEOUT) as client:
                resp = client.post(register_url, json=payload)

                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("registered", False)
                return False

        except Exception:
            return False

    def discover_and_register(self) -> Tuple[bool, Optional[str]]:
        """
        Try multiple discovery methods to find and register with GhostHub.

        Returns:
            (success: bool, discovered_url: Optional[str])
        """
        logger.info("[GhostHub] Attempting auto-discovery...")

        # Build candidate URLs
        candidate_urls: List[Tuple[str, str]] = []
        seen_urls = set()

        # 1. Configured URL (highest priority)
        configured_url = os.environ.get('GHOSTHUB_URL', self.ghosthub_url)
        if configured_url:
            normalized_url = configured_url.rstrip("/")
            candidate_urls.append(("configured URL", normalized_url))
            seen_urls.add(normalized_url)

        # 2. Discovery URLs (skip duplicates)
        for url in DISCOVERY_URLS:
            if url not in seen_urls:
                candidate_urls.append(("discovery", url))
                seen_urls.add(url)

        # Try each URL
        for source, url in candidate_urls:
            logger.debug(f"[GhostHub]   Trying {source}: {url}...")

            if self._try_url(url):
                logger.info(f"[GhostHub] ✓ Registered successfully via {source}")
                return True, url
            else:
                logger.debug(f"[GhostHub]   ✗ Failed: {url}")

        # All attempts failed
        logger.warning("[GhostHub] ✗ Could not discover GhostHub on network")
        logger.warning("[GhostHub]   This is OK - GhostHub can still add this server manually")
        return False, None

    async def start_periodic_registration_with_discovery(self, interval_seconds: int = 300) -> None:
        """Start periodic re-registration with auto-discovery."""
        self._stop_event = False

        # Initial discovery on startup (skip if already discovered)
        if not self._discovered_url:
            success, discovered_url = await asyncio.get_event_loop().run_in_executor(
                None, self.discover_and_register
            )

            if success:
                self._discovered_url = discovered_url

        # Periodic re-registration
        failures = 0
        while not self._stop_event:
            await asyncio.sleep(interval_seconds)

            if not self._stop_event:
                # Try using previously discovered URL first
                if self._discovered_url:
                    # Temporarily set ghosthub_url to discovered URL
                    original_url = self.ghosthub_url
                    self.ghosthub_url = self._discovered_url
                    success = await asyncio.get_event_loop().run_in_executor(
                        None, self.register
                    )
                    self.ghosthub_url = original_url
                else:
                    success = False

                # If re-registration failed after multiple attempts, try discovery again
                if not success and failures >= 3:
                    # Re-discover after multiple failures
                    logger.info(f"[GhostHub] Re-attempting discovery after {failures} failures...")
                    success, new_url = await asyncio.get_event_loop().run_in_executor(
                        None, self.discover_and_register
                    )
                    if success:
                        self._discovered_url = new_url
                        failures = 0

                # Track failures
                if success:
                    if failures > 0:
                        logger.info(f"[GhostHub] ✓ Re-registered after {failures} failures")
                    failures = 0
                else:
                    failures += 1
                    # Only log every 5 failures to reduce spam
                    if failures % 5 == 0:
                        logger.debug(f"[GhostHub] Still unable to reach GhostHub ({failures} failures)")

    async def start_periodic_registration(self, interval_seconds: int = 300) -> None:
        """Start periodic re-registration with GhostHub."""
        self._stop_event = False
        ghosthub_url = os.environ.get('GHOSTHUB_URL', self.ghosthub_url)
        
        # Try registration once on startup
        logger.info(f"[GhostHub] Attempting to register with GhostHub at {ghosthub_url}")
        success = await asyncio.get_event_loop().run_in_executor(None, self.register)
        
        if success:
            logger.info(f"[GhostHub] ✓ Registered successfully with GhostHub")
        else:
            # Single clear message instead of retry spam
            logger.warning(f"[GhostHub] ✗ Could not register with GhostHub at {ghosthub_url}")
            logger.warning(f"[GhostHub]   This is OK - GhostHub can still find this server via manual add.")
            logger.warning(f"[GhostHub]   To fix: ensure GhostStream is on same network as GhostHub,")
            logger.warning(f"[GhostHub]   or set GHOSTHUB_URL environment variable to correct address.")
        
        # Periodic re-registration (silent - only log on success or after long failure)
        failures = 0
        while not self._stop_event:
            await asyncio.sleep(interval_seconds)
            if not self._stop_event:
                if await asyncio.get_event_loop().run_in_executor(None, self.register):
                    if failures > 0:
                        logger.info(f"[GhostHub] ✓ Re-registered with GhostHub after {failures} failures")
                    failures = 0
                else:
                    failures += 1
                    # Only log every 5 failures to reduce spam
                    if failures % 5 == 0:
                        logger.debug(f"[GhostHub] Still unable to reach GhostHub ({failures} failures)")
    
    def stop(self) -> None:
        """Stop periodic registration."""
        self._stop_event = True
        logger.info("Stopped GhostHub registration")

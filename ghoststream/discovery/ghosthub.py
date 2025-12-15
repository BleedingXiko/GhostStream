"""
GhostHub registration for GhostStream
"""

import socket
import logging
import asyncio
import os
from typing import Dict, Any

from ..config import get_config
from ..hardware import get_capabilities

logger = logging.getLogger(__name__)


class GhostHubRegistration:
    """Handles push registration with GhostHub server."""
    
    def __init__(self, ghosthub_url: str, port: int = 8765):
        self.ghosthub_url = ghosthub_url.rstrip("/")
        self.port = port
        self._stop_event = False
        self._registration_task = None
    
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

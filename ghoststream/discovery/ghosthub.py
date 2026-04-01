import socket
import logging
import gevent
import os
import sys
from typing import Dict, Any, Optional

from ..config import get_config
from ..hardware import get_capabilities
from ..security import RegistrationAuthService

logger = logging.getLogger(__name__)

class GhostHubRegistration:
    """Handles push registration with GhostHub server."""
    
    def __init__(
        self,
        ghosthub_url: str,
        port: int = 8765,
        callback_url: Optional[str] = None,
        auth_service: Optional[RegistrationAuthService] = None,
    ):
        self.ghosthub_url = ghosthub_url.rstrip("/")
        self.port = port
        self.callback_url = callback_url
        self.auth_service = auth_service
        self._stop_event = False
        self._registration_task = None
    
    def _get_local_ip(self, target_host: str) -> str:
        """
        Smart IP detection: Find the local IP that can actually reach the target host.
        Fixes issues where Mac is connected to multiple networks (Home WiFi + Pi WiFi).
        """
        try:
            # Strip protocol if present
            host = target_host.replace("http://", "").replace("https://", "").split(":")[0]
            
            # Create a socket and "connect" it to the target host
            # This doesn't send data, it just makes the OS pick the best interface
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                # Use a common port (80) to probe the route
                s.connect((host, 80))
                ip = s.getsockname()[0]
                return ip
            finally:
                s.close()
        except Exception as e:
            logger.debug(f"Smart IP detection failed for {target_host}: {e}")
            
        # Fallback to standard detection
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
        
        # Determine the correct local IP based on where GhostHub is
        local_ip = self._get_local_ip(self.ghosthub_url)
        
        config = get_config()
        # Authoritative source for the URL
        advertised_url = config.server.advertised_url
        
        # If callback_url matches dumb detection but smart detection found something else,
        # prefer the smart one unless advertiser_url is set.
        final_callback = self.callback_url
        if not advertised_url and final_callback:
             host_part = final_callback.replace("http://", "").replace("https://", "").split(":")[0]
             if host_part != local_ip:
                 logger.debug(f"[GhostHub] Overriding auto-base_url {final_callback} with smart-detected {local_ip}")
                 final_callback = f"http://{local_ip}:{self.port}"
        
        # Fallback to smart-detected URL
        final_callback = final_callback or f"http://{local_ip}:{self.port}"
        
        logger.info(f"[GhostHub] Registering server at {local_ip}:{self.port} with callback: {final_callback}")
        
        return {
            "address": f"{local_ip}:{self.port}",
            "callback_url": final_callback,
            "name": config.mdns.service_name,
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
            headers = {}
            if self.auth_service:
                payload = self.auth_service.sign_payload(payload)
                headers = self.auth_service.build_headers(payload)
            
            logger.info(f"[GhostHub] Attempting registration at {register_url}...")
            
            # Longer timeout for slow hotspot connections
            with httpx.Client(timeout=10.0) as client:
                resp = client.post(register_url, json=payload, headers=headers)
                
                if resp.is_success:
                    data = resp.json()
                    if data.get("registered", True): # Assume True if missing but 2xx for now
                        logger.info(f"[GhostHub] ✓ Successfully registered with GhostHub as {payload['name']}")
                        return True
                    else:
                        logger.warning(f"[GhostHub] ✗ GhostHub rejected registration: {data.get('reason', 'Unknown reason')}")
                        return False
                else:
                    logger.warning(f"[GhostHub] ✗ Registration failed with status {resp.status_code}")
                    if resp.status_code == 401 or resp.status_code == 403:
                        logger.warning("[GhostHub]   -> Authentication failure. Check registration_secret/api_key.")
                    elif resp.status_code == 404:
                        logger.warning(f"[GhostHub]   -> Registration endpoint not found at {register_url}")
                    else:
                        logger.warning(f"[GhostHub]   -> Server response: {resp.text[:200]}")
                    return False
                    
        except httpx.ConnectError:
            logger.warning(f"[GhostHub] ✗ Cannot reach GhostHub at {ghosthub_url} (Check IP/Port)")
            return False
        except httpx.TimeoutException:
            logger.warning(f"[GhostHub] ✗ GhostHub connection timed out (Check network/firewall)")
            return False
        except Exception as e:
            logger.warning(f"[GhostHub] ✗ Unexpected registration error: {e}")
            return False
    
    def start_periodic_registration(self, interval_seconds: int = 60) -> None:
        """Start periodic re-registration with GhostHub."""
        self._stop_event = False
        
        # Immediate first attempt
        self.register()
        
        # Periodic loop
        while not self._stop_event:
            gevent.sleep(interval_seconds)
            if not self._stop_event:
                self.register()
    
    def unregister(self) -> bool:
        """Unregister this GhostStream instance from GhostHub."""
        import httpx
        
        # Allow override via environment variable
        ghosthub_url = os.environ.get('GHOSTHUB_URL', self.ghosthub_url)
        unregister_url = f"{ghosthub_url}/api/ghoststream/servers/unregister"
        
        try:
            config = get_config()
            payload = {"name": config.mdns.service_name}
            headers = {}
            if self.auth_service:
                payload = self.auth_service.sign_payload(payload)
                headers = self.auth_service.build_headers(payload)
                
            logger.info(f"[GhostHub] Attempting unregistration at {unregister_url}...")
            
            with httpx.Client(timeout=5.0) as client:
                resp = client.post(unregister_url, json=payload, headers=headers)
                if resp.is_success:
                    logger.info(f"[GhostHub] ✓ Successfully unregistered from GhostHub")
                    return True
                else:
                    logger.debug(f"[GhostHub] Unregistration returned {resp.status_code}")
                    return False
        except Exception as e:
            logger.debug(f"[GhostHub] Unregistration failed (ignoring on shutdown): {e}")
            return False

    def stop(self) -> None:
        """Stop periodic registration and unregister from GhostHub."""
        self._stop_event = True
        self.unregister()
        logger.info("Stopped GhostHub registration")

"""
mDNS/Zeroconf service discovery for GhostStream
"""

import socket
import logging
from typing import Optional, Dict, Any
from zeroconf import ServiceInfo, Zeroconf, ServiceBrowser, ServiceListener
import json

from .config import get_config
from .hardware import get_capabilities

logger = logging.getLogger(__name__)


class GhostStreamService:
    """Advertises GhostStream service via mDNS/Zeroconf."""
    
    SERVICE_TYPE = "_ghoststream._tcp.local."
    
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.config = get_config()
        self.zeroconf: Optional[Zeroconf] = None
        self.service_info: Optional[ServiceInfo] = None
        
    def _get_local_ip(self) -> str:
        """Get the local IP address."""
        try:
            # Create a socket to determine the local IP
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"
    
    def _build_properties(self) -> Dict[bytes, bytes]:
        """Build service properties for mDNS TXT record."""
        capabilities = get_capabilities()
        
        # Get available hw accels
        hw_accels = [
            hw.type.value for hw in capabilities.hw_accels
            if hw.available
        ]
        
        properties = {
            b"version": b"1.0.0",
            b"api_version": b"1",
            b"hw_accels": ",".join(hw_accels).encode(),
            b"video_codecs": ",".join(capabilities.video_codecs).encode(),
            b"audio_codecs": ",".join(capabilities.audio_codecs).encode(),
            b"max_jobs": str(capabilities.max_concurrent_jobs).encode(),
            b"platform": capabilities.platform.encode()[:255],
        }
        
        return properties
    
    def start(self) -> bool:
        """Start advertising the service via mDNS."""
        if not self.config.mdns.enabled:
            logger.info("mDNS is disabled in configuration")
            return False
        
        try:
            self.zeroconf = Zeroconf()
            
            # Get local IP
            local_ip = self._get_local_ip() if self.host == "0.0.0.0" else self.host
            
            # Create service name
            service_name = self.config.mdns.service_name.replace(" ", "-")
            full_name = f"{service_name}.{self.SERVICE_TYPE}"
            
            # Build properties
            properties = self._build_properties()
            
            self.service_info = ServiceInfo(
                type_=self.SERVICE_TYPE,
                name=full_name,
                addresses=[socket.inet_aton(local_ip)],
                port=self.port,
                properties=properties,
                server=f"{service_name}.local."
            )
            
            self.zeroconf.register_service(self.service_info)
            
            logger.info(f"mDNS service registered: {full_name}")
            logger.info(f"Service available at http://{local_ip}:{self.port}")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to start mDNS service: {e}")
            return False
    
    def stop(self) -> None:
        """Stop advertising the service."""
        if self.zeroconf and self.service_info:
            try:
                self.zeroconf.unregister_service(self.service_info)
                self.zeroconf.close()
                logger.info("mDNS service unregistered")
            except Exception as e:
                logger.error(f"Error stopping mDNS service: {e}")
        
        self.zeroconf = None
        self.service_info = None
        self._udp_running = False
    
    def start_udp_responder(self) -> None:
        """Start UDP broadcast responder for discovery fallback."""
        import threading
        self._udp_running = True
        thread = threading.Thread(target=self._udp_responder_loop, daemon=True)
        thread.start()
        logger.info(f"[Discovery] UDP responder started on port 8766")
    
    def _udp_responder_loop(self) -> None:
        """Listen for UDP discovery broadcasts and respond."""
        UDP_PORT = 8766
        
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(('', UDP_PORT))
            sock.settimeout(1.0)  # Allow periodic check of _udp_running
            
            capabilities = get_capabilities()
            hw_accels = [hw.type.value for hw in capabilities.hw_accels if hw.available]
            
            while self._udp_running:
                try:
                    data, addr = sock.recvfrom(1024)
                    if data == b'GHOSTSTREAM_DISCOVER':
                        # Build response: GHOSTSTREAM_ANNOUNCE:port:version:hw_accels
                        response = f"GHOSTSTREAM_ANNOUNCE:{self.port}:1.0.0:{','.join(hw_accels)}"
                        sock.sendto(response.encode(), addr)
                        logger.debug(f"[Discovery] Responded to UDP discovery from {addr}")
                except socket.timeout:
                    continue
                except Exception as e:
                    logger.debug(f"[Discovery] UDP responder error: {e}")
            
            sock.close()
        except Exception as e:
            logger.error(f"[Discovery] Failed to start UDP responder: {e}")


class GhostStreamDiscovery(ServiceListener):
    """Discovers other GhostStream services on the network."""
    
    SERVICE_TYPE = "_ghoststream._tcp.local."
    
    def __init__(self):
        self.zeroconf: Optional[Zeroconf] = None
        self.browser: Optional[ServiceBrowser] = None
        self.services: Dict[str, Dict[str, Any]] = {}
        self.callbacks: list = []
    
    def add_callback(self, callback) -> None:
        """Add a callback for service discovery events."""
        self.callbacks.append(callback)
    
    def start(self) -> None:
        """Start discovering GhostStream services."""
        try:
            self.zeroconf = Zeroconf()
            self.browser = ServiceBrowser(
                self.zeroconf,
                self.SERVICE_TYPE,
                self
            )
            logger.info("Started GhostStream service discovery")
        except Exception as e:
            logger.error(f"Failed to start service discovery: {e}")
    
    def stop(self) -> None:
        """Stop service discovery."""
        if self.browser:
            self.browser.cancel()
        if self.zeroconf:
            self.zeroconf.close()
        
        self.browser = None
        self.zeroconf = None
        logger.info("Stopped GhostStream service discovery")
    
    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        """Called when a service is discovered."""
        info = zc.get_service_info(type_, name)
        if info:
            addresses = [socket.inet_ntoa(addr) for addr in info.addresses]
            
            service_data = {
                "name": name,
                "host": addresses[0] if addresses else None,
                "port": info.port,
                "properties": {
                    k.decode(): v.decode() if isinstance(v, bytes) else v
                    for k, v in info.properties.items()
                }
            }
            
            self.services[name] = service_data
            logger.info(f"Discovered GhostStream service: {name} at {addresses[0]}:{info.port}")
            
            for callback in self.callbacks:
                try:
                    callback("added", service_data)
                except Exception as e:
                    logger.error(f"Callback error: {e}")
    
    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        """Called when a service is removed."""
        if name in self.services:
            service_data = self.services.pop(name)
            logger.info(f"GhostStream service removed: {name}")
            
            for callback in self.callbacks:
                try:
                    callback("removed", service_data)
                except Exception as e:
                    logger.error(f"Callback error: {e}")
    
    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        """Called when a service is updated."""
        self.add_service(zc, type_, name)
    
    def get_services(self) -> Dict[str, Dict[str, Any]]:
        """Get all discovered services."""
        return self.services.copy()


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
        from . import __version__
        
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
        import os
        
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
                    
        except httpx.ConnectError as e:
            logger.warning(f"[GhostHub] Cannot connect to {ghosthub_url} - is GhostHub running? Error: {e}")
            return False
        except httpx.TimeoutException:
            logger.warning(f"[GhostHub] Connection to {ghosthub_url} timed out after 15s")
            return False
        except Exception as e:
            logger.warning(f"[GhostHub] Registration error: {e}")            
            return False
    
    async def start_periodic_registration(self, interval_seconds: int = 300) -> None:
        """Start periodic re-registration with GhostHub."""
        import asyncio
        import os
        
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

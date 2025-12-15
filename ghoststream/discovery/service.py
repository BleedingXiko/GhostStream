"""
mDNS/Zeroconf service advertisement for GhostStream
"""

import socket
import logging
import threading
from typing import Optional, Dict

from zeroconf import ServiceInfo, Zeroconf

from ..config import get_config
from ..hardware import get_capabilities

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
        self._udp_running = False
        
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

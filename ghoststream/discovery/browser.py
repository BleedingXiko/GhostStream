"""
Service discovery browser for GhostStream
"""

import socket
import logging
from typing import Optional, Dict, Any

from zeroconf import Zeroconf, ServiceBrowser, ServiceListener

logger = logging.getLogger(__name__)


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

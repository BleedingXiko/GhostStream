"""Adapters for GhostStream mDNS advertisement."""

from __future__ import annotations

import logging
import socket
from typing import Dict, Optional

from zeroconf import ServiceInfo, Zeroconf

logger = logging.getLogger(__name__)


class ZeroconfServiceAdvertiser:
    """Registers and unregisters the GhostStream mDNS service."""

    SERVICE_TYPE = "_ghoststream._tcp.local."

    def __init__(self):
        self.zeroconf: Optional[Zeroconf] = None
        self.service_info: Optional[ServiceInfo] = None

    def start(self, *, service_name: str, host: str, port: int, properties: Dict[bytes, bytes]) -> bool:
        if self.zeroconf is not None:
            return True

        try:
            self.zeroconf = Zeroconf()

            local_ip = self._resolve_service_host(host)
            normalized_name = service_name.replace(" ", "-")
            full_name = f"{normalized_name}.{self.SERVICE_TYPE}"

            self.service_info = ServiceInfo(
                type_=self.SERVICE_TYPE,
                name=full_name,
                addresses=[socket.inet_aton(local_ip)],
                port=port,
                properties=properties,
                server=f"{normalized_name}.local.",
            )

            self.zeroconf.register_service(self.service_info)

            logger.info("mDNS service registered: %s", full_name)
            logger.info("Service available at http://%s:%s", local_ip, port)
            return True
        except Exception as exc:
            logger.error("Failed to start mDNS service: %s", exc)
            self.stop()
            return False

    def stop(self) -> None:
        if self.zeroconf and self.service_info:
            try:
                self.zeroconf.unregister_service(self.service_info)
                logger.info("mDNS service unregistered")
            except Exception as exc:
                logger.error("Error stopping mDNS service: %s", exc)
            finally:
                try:
                    self.zeroconf.close()
                except Exception:
                    pass
        elif self.zeroconf:
            try:
                self.zeroconf.close()
            except Exception:
                pass

        self.zeroconf = None
        self.service_info = None

    def _resolve_service_host(self, host: str) -> str:
        if host != "0.0.0.0":
            return host

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.connect(("8.8.8.8", 80))
                return sock.getsockname()[0]
            finally:
                sock.close()
        except Exception:
            return "127.0.0.1"

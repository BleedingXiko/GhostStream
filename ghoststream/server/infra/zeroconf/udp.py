"""Adapters for GhostStream UDP LAN discovery fallback."""

from __future__ import annotations

import logging
from typing import Callable, Optional

import gevent
from gevent import socket

logger = logging.getLogger(__name__)


class UDPDiscoveryResponder:
    """Responds to UDP discovery probes on the local network."""

    def __init__(self, *, port: int = 8766, response_factory: Callable[[], bytes]):
        self.port = port
        self.response_factory = response_factory
        self._running = False
        self._greenlet: Optional[gevent.Greenlet] = None

    def start(self) -> None:
        if self._running:
            return

        self._running = True
        self._greenlet = gevent.spawn(self._serve)
        logger.info("[Discovery] UDP responder started on port %s", self.port)

    def stop(self) -> None:
        self._running = False
        if self._greenlet is not None:
            self._greenlet.join(timeout=2.0)
            if not self._greenlet.dead:
                self._greenlet.kill(block=False)
        self._greenlet = None

    def _serve(self) -> None:
        sock: Optional[socket.socket] = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("", self.port))
            sock.settimeout(1.0)

            while self._running:
                try:
                    data, addr = sock.recvfrom(1024)
                    if data == b"GHOSTSTREAM_DISCOVER":
                        sock.sendto(self.response_factory(), addr)
                        logger.debug("[Discovery] Responded to UDP discovery from %s", addr)
                except socket.timeout:
                    continue
                except gevent.GreenletExit:
                    break
                except Exception as exc:
                    logger.debug("[Discovery] UDP responder error: %s", exc)
        except Exception as exc:
            logger.error("[Discovery] Failed to start UDP responder: %s", exc)
        finally:
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass

"""Long-lived discovery and registration services for GhostStream."""

from __future__ import annotations

import logging
import os
import socket
from typing import Any, Callable, Dict, Optional

import gevent

from ... import __version__
from ..domain.security.registration import RegistrationAuthService
from ..infra.zeroconf.advertiser import ZeroconfServiceAdvertiser
from ..infra.zeroconf.udp import UDPDiscoveryResponder

logger = logging.getLogger(__name__)


class GhostStreamDiscoveryService:
    """Owns LAN discovery advertisement for the active server runtime."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        service_name: str,
        capabilities_factory: Callable[[], Any],
    ):
        self.host = host
        self.port = port
        self.service_name = service_name
        self.capabilities_factory = capabilities_factory
        self._advertiser = ZeroconfServiceAdvertiser()
        self._udp_responder = UDPDiscoveryResponder(response_factory=self._build_udp_announcement)

    def start(self) -> bool:
        return self._advertiser.start(
            service_name=self.service_name,
            host=self.host,
            port=self.port,
            properties=self._build_properties(),
        )

    def stop(self) -> None:
        self._udp_responder.stop()
        self._advertiser.stop()

    def start_udp_responder(self) -> None:
        self._udp_responder.start()

    def _build_properties(self) -> Dict[bytes, bytes]:
        capabilities = self.capabilities_factory()
        hw_accels = [hw.type.value for hw in capabilities.hw_accels if hw.available]

        return {
            b"version": b"1.0.0",
            b"api_version": b"1",
            b"hw_accels": ",".join(hw_accels).encode(),
            b"video_codecs": ",".join(capabilities.video_codecs).encode(),
            b"audio_codecs": ",".join(capabilities.audio_codecs).encode(),
            b"max_jobs": str(capabilities.max_concurrent_jobs).encode(),
            b"platform": capabilities.platform.encode()[:255],
        }

    def _build_udp_announcement(self) -> bytes:
        capabilities = self.capabilities_factory()
        hw_accels = [hw.type.value for hw in capabilities.hw_accels if hw.available]
        response = f"GHOSTSTREAM_ANNOUNCE:{self.port}:1.0.0:{','.join(hw_accels)}"
        return response.encode()


class GhostHubRegistrationService:
    """Handles GhostHub push registration for the active server runtime."""

    def __init__(
        self,
        *,
        ghosthub_url: str,
        port: int,
        callback_url: Optional[str],
        service_name: str,
        advertised_url: Optional[str],
        capabilities_factory: Callable[[], Any],
        auth_service: Optional[RegistrationAuthService] = None,
    ):
        self.ghosthub_url = ghosthub_url.rstrip("/")
        self.port = port
        self.callback_url = callback_url
        self.service_name = service_name
        self.advertised_url = advertised_url
        self.capabilities_factory = capabilities_factory
        self.auth_service = auth_service
        self._stop_event = False

    def register(self) -> bool:
        import httpx

        ghosthub_url = os.environ.get("GHOSTHUB_URL") or self.ghosthub_url
        register_url = f"{ghosthub_url}/api/ghoststream/servers/register"

        try:
            payload = self._build_registration_payload(ghosthub_url)
            headers = {}
            if self.auth_service:
                payload = self.auth_service.sign_payload(payload)
                headers = self.auth_service.build_headers(payload)

            logger.info("[GhostHub] Attempting registration at %s...", register_url)

            with httpx.Client(timeout=10.0) as client:
                response = client.post(register_url, json=payload, headers=headers)

            if response.is_success:
                data = response.json()
                if data.get("registered", True):
                    logger.info(
                        "[GhostHub] ✓ Successfully registered with GhostHub as %s",
                        payload["name"],
                    )
                    return True

                logger.warning(
                    "[GhostHub] ✗ GhostHub rejected registration: %s",
                    data.get("reason", "Unknown reason"),
                )
                return False

            logger.warning("[GhostHub] ✗ Registration failed with status %s", response.status_code)
            if response.status_code in (401, 403):
                logger.warning(
                    "[GhostHub]   -> Authentication failure. Check registration_secret/api_key."
                )
            elif response.status_code == 404:
                logger.warning(
                    "[GhostHub]   -> Registration endpoint not found at %s",
                    register_url,
                )
            else:
                logger.warning("[GhostHub]   -> Server response: %s", response.text[:200])
            return False
        except httpx.ConnectError:
            logger.warning("[GhostHub] ✗ Cannot reach GhostHub at %s (Check IP/Port)", ghosthub_url)
            return False
        except httpx.TimeoutException:
            logger.warning("[GhostHub] ✗ GhostHub connection timed out (Check network/firewall)")
            return False
        except Exception as exc:
            logger.warning("[GhostHub] ✗ Unexpected registration error: %s", exc)
            return False

    def start_periodic_registration(self, interval_seconds: int = 60) -> None:
        self._stop_event = False
        self.register()

        while not self._stop_event:
            gevent.sleep(interval_seconds)
            if not self._stop_event:
                self.register()

    def unregister(self) -> bool:
        import httpx

        ghosthub_url = os.environ.get("GHOSTHUB_URL") or self.ghosthub_url
        unregister_url = f"{ghosthub_url}/api/ghoststream/servers/unregister"

        try:
            payload = {"name": self.service_name}
            headers = {}
            if self.auth_service:
                payload = self.auth_service.sign_payload(payload)
                headers = self.auth_service.build_headers(payload)

            logger.info("[GhostHub] Attempting unregistration at %s...", unregister_url)

            with httpx.Client(timeout=5.0) as client:
                response = client.post(unregister_url, json=payload, headers=headers)

            if response.is_success:
                logger.info("[GhostHub] ✓ Successfully unregistered from GhostHub")
                return True

            logger.debug("[GhostHub] Unregistration returned %s", response.status_code)
            return False
        except Exception as exc:
            logger.debug("[GhostHub] Unregistration failed (ignoring on shutdown): %s", exc)
            return False

    def stop(self) -> None:
        self._stop_event = True
        self.unregister()
        logger.info("Stopped GhostHub registration")

    def _build_registration_payload(self, ghosthub_url: str) -> Dict[str, Any]:
        capabilities = self.capabilities_factory()
        hw_accels = [hw.type.value for hw in capabilities.hw_accels if hw.available]

        local_ip = self._get_local_ip(ghosthub_url)
        final_callback = self.callback_url

        if not self.advertised_url and final_callback:
            host_part = final_callback.replace("http://", "").replace("https://", "").split(":")[0]
            if host_part != local_ip:
                logger.debug(
                    "[GhostHub] Overriding auto-base_url %s with smart-detected %s",
                    final_callback,
                    local_ip,
                )
                final_callback = f"http://{local_ip}:{self.port}"

        final_callback = final_callback or f"http://{local_ip}:{self.port}"

        logger.info(
            "[GhostHub] Registering server at %s:%s with callback: %s",
            local_ip,
            self.port,
            final_callback,
        )

        return {
            "address": f"{local_ip}:{self.port}",
            "callback_url": final_callback,
            "name": self.service_name,
            "version": __version__,
            "hw_accels": hw_accels,
            "video_codecs": capabilities.video_codecs,
            "audio_codecs": capabilities.audio_codecs,
            "max_jobs": capabilities.max_concurrent_jobs,
        }

    def _get_local_ip(self, target_host: str) -> str:
        try:
            host = target_host.replace("http://", "").replace("https://", "").split(":")[0]
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.connect((host, 80))
                return sock.getsockname()[0]
            finally:
                sock.close()
        except Exception as exc:
            logger.debug("Smart IP detection failed for %s: %s", target_host, exc)

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.connect(("8.8.8.8", 80))
                return sock.getsockname()[0]
            finally:
                sock.close()
        except Exception:
            return "127.0.0.1"

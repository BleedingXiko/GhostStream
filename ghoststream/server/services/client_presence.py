"""Long-lived HTTP client presence tracking for GhostStream."""

from __future__ import annotations

import time


class ClientPresenceService:
    HTTP_CLIENT_TTL = 300.0

    def __init__(self) -> None:
        self._http_clients: dict[str, float] = {}

    def seen(self, client_name: str) -> None:
        if client_name:
            self._http_clients[client_name] = time.time()

    def get_active_names(self) -> list[str]:
        now = time.time()
        active = []
        for name, last_seen in list(self._http_clients.items()):
            if now - last_seen > self.HTTP_CLIENT_TTL:
                del self._http_clients[name]
                continue
            active.append(name)
        return sorted(active)

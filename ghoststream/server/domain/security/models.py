"""Domain models for GhostStream security state."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NodeIdentity:
    """Persisted GhostStream node identity."""

    node_id: str
    secret: str
    created_at: int

    def secret_bytes(self) -> bytes:
        return self.secret.encode("utf-8")

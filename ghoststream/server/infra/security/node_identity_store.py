"""Persistence adapter for GhostStream node identity."""

from __future__ import annotations

import json
import secrets
import time
import uuid
from pathlib import Path

from ...domain.security.models import NodeIdentity


class NodeIdentityStore:
    """Loads or creates the persisted GhostStream node identity."""

    def __init__(self, state_dir: Path):
        self.state_dir = state_dir
        self.identity_path = self.state_dir / "node_identity.json"

    def load_or_create(self) -> NodeIdentity:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        if self.identity_path.exists():
            payload = json.loads(self.identity_path.read_text(encoding="utf-8"))
            return NodeIdentity(
                node_id=payload["node_id"],
                secret=payload["secret"],
                created_at=int(payload["created_at"]),
            )

        identity = NodeIdentity(
            node_id=str(uuid.uuid4()),
            secret=secrets.token_urlsafe(48),
            created_at=int(time.time()),
        )
        self.identity_path.write_text(
            json.dumps(
                {
                    "node_id": identity.node_id,
                    "secret": identity.secret,
                    "created_at": identity.created_at,
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        return identity

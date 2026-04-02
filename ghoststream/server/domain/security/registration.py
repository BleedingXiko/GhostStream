"""Domain logic for authenticated GhostHub registration payloads."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any, Dict, Optional

from .models import NodeIdentity


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _stable_json(data: Dict[str, Any]) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")


class RegistrationAuthService:
    """Builds signed registration payloads and auth headers."""

    def __init__(self, identity: NodeIdentity, shared_secret: Optional[str] = None):
        self.identity = identity
        self.shared_secret = shared_secret

    def sign_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        signed = dict(payload)
        signed["node_id"] = self.identity.node_id
        signed["timestamp"] = int(time.time())
        signed["nonce"] = secrets.token_urlsafe(16)
        signed["signature"] = self._sign_struct(signed)
        return signed

    def build_headers(self, payload: Dict[str, Any]) -> Dict[str, str]:
        headers = {
            "X-GhostStream-Node-Id": self.identity.node_id,
            "X-GhostStream-Registration-Signature": payload["signature"],
            "X-GhostStream-Registration-Timestamp": str(payload["timestamp"]),
            "X-GhostStream-Registration-Nonce": payload["nonce"],
        }
        if self.shared_secret:
            headers["X-GhostStream-Registration-Secret"] = self.shared_secret
        return headers

    def _sign_struct(self, payload: Dict[str, Any]) -> str:
        material = {
            key: value
            for key, value in payload.items()
            if key != "signature"
        }
        digest = hmac.new(
            self.identity.secret_bytes(),
            _stable_json(material),
            hashlib.sha256,
        ).digest()
        return _b64url_encode(digest)

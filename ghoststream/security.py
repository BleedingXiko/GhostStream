"""
GhostStream security primitives.

This module owns persisted node identity, authenticated registration signing,
and job-scoped capability minting/validation.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from .config import get_config


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode((data + padding).encode("ascii"))


def _stable_json(data: Dict[str, Any]) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")


@dataclass(frozen=True)
class NodeIdentity:
    """Persisted GhostStream node identity."""

    node_id: str
    secret: str
    created_at: int

    def secret_bytes(self) -> bytes:
        return self.secret.encode("utf-8")


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


class CapabilityError(Exception):
    """Raised when a GhostStream capability is invalid."""


class CapabilityService:
    """Issues and validates GhostStream job-scoped capabilities."""

    STREAM_SCOPE = "stream"
    CONTROL_SCOPE = "control"

    def __init__(self, secret: bytes, *, node_id: str, default_ttl_seconds: int = 21600):
        self._secret = secret
        self._node_id = node_id
        self._default_ttl_seconds = default_ttl_seconds
        self._revoked_jobs: set[str] = set()
        self._revoked_tokens: set[str] = set()

    def mint(
        self,
        *,
        job_id: str,
        scope: str,
        ttl_seconds: Optional[int] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> str:
        issued_at = int(time.time())
        payload = {
            "jti": secrets.token_urlsafe(12),
            "job_id": job_id,
            "scope": scope,
            "iat": issued_at,
            "exp": issued_at + int(ttl_seconds or self._default_ttl_seconds),
            "node_id": self._node_id,
        }
        if extra:
            payload.update(extra)

        encoded_payload = _b64url_encode(_stable_json(payload))
        signature = _b64url_encode(
            hmac.new(self._secret, encoded_payload.encode("ascii"), hashlib.sha256).digest()
        )
        return f"{encoded_payload}.{signature}"

    def validate(
        self,
        token: str,
        *,
        required_scope: str,
        job_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not token:
            raise CapabilityError("Missing capability token")

        try:
            encoded_payload, encoded_signature = token.split(".", 1)
        except ValueError as exc:
            raise CapabilityError("Malformed capability token") from exc

        expected_signature = _b64url_encode(
            hmac.new(self._secret, encoded_payload.encode("ascii"), hashlib.sha256).digest()
        )
        if not secrets.compare_digest(encoded_signature, expected_signature):
            raise CapabilityError("Invalid capability signature")

        payload = json.loads(_b64url_decode(encoded_payload).decode("utf-8"))
        now = int(time.time())

        if payload.get("jti") in self._revoked_tokens:
            raise CapabilityError("Capability has been revoked")
        if payload.get("job_id") in self._revoked_jobs:
            raise CapabilityError("Job capability has been revoked")
        if payload.get("scope") != required_scope:
            raise CapabilityError("Capability scope does not match the requested action")
        if job_id and payload.get("job_id") != job_id:
            raise CapabilityError("Capability does not match the requested job")
        if int(payload.get("exp", 0)) < now:
            raise CapabilityError("Capability has expired")

        return payload

    def revoke_token(self, token: str) -> None:
        try:
            encoded_payload, _ = token.split(".", 1)
            payload = json.loads(_b64url_decode(encoded_payload).decode("utf-8"))
            jti = payload.get("jti")
            if jti:
                self._revoked_tokens.add(jti)
        except Exception:
            return

    def revoke_job(self, job_id: str) -> None:
        self._revoked_jobs.add(job_id)

    def build_stream_url(self, base_stream_url: str, token: str) -> str:
        separator = "&" if "?" in base_stream_url else "?"
        return f"{base_stream_url}{separator}gst={token}"


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


_capability_service: Optional[CapabilityService] = None
_node_identity: Optional[NodeIdentity] = None


def get_state_directory() -> Path:
    config = get_config()
    return Path(config.security.state_directory).expanduser()


def get_node_identity() -> NodeIdentity:
    global _node_identity
    if _node_identity is None:
        store = NodeIdentityStore(get_state_directory())
        _node_identity = store.load_or_create()
    return _node_identity


def get_capability_service() -> CapabilityService:
    global _capability_service
    if _capability_service is None:
        config = get_config()
        identity = get_node_identity()
        _capability_service = CapabilityService(
            identity.secret_bytes(),
            node_id=identity.node_id,
            default_ttl_seconds=config.security.job_token_ttl_seconds,
        )
    return _capability_service


def set_capability_service(service: CapabilityService) -> None:
    global _capability_service
    _capability_service = service

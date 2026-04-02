"""Domain logic for GhostStream job-scoped capabilities."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any, Dict, Optional


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode((data + padding).encode("ascii"))


def _stable_json(data: Dict[str, Any]) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")


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

"""Request-level capability enforcement for Flask — Specter-native."""

from __future__ import annotations

from flask import request, abort

from ..security import CapabilityError, CapabilityService, get_capability_service


CONTROL_HEADER = "X-GhostStream-Control-Token"
STREAM_HEADER = "X-GhostStream-Stream-Token"
STREAM_QUERY_PARAM = "gst"


def _extract_control_token() -> str | None:
    return request.headers.get(CONTROL_HEADER)


def _extract_stream_token() -> str | None:
    return (
        request.args.get(STREAM_QUERY_PARAM)
        or request.headers.get(STREAM_HEADER)
    )


def require_control_capability(job_id: str) -> None:
    """Enforce control-scope capability for the given job."""
    token = _extract_control_token()
    try:
        get_capability_service().validate(
            token or "",
            required_scope=CapabilityService.CONTROL_SCOPE,
            job_id=job_id,
        )
    except CapabilityError as exc:
        abort(403, description=str(exc))


def require_stream_capability(job_id: str) -> str:
    """Enforce stream-scope capability for the given job."""
    token = _extract_stream_token()
    try:
        get_capability_service().validate(
            token or "",
            required_scope=CapabilityService.STREAM_SCOPE,
            job_id=job_id,
        )
    except CapabilityError as exc:
        abort(403, description=str(exc))
    return token or ""


def append_token_to_playlist(content: str, token: str) -> str:
    """Rewrite relative playlist entries so HLS players keep the stream token."""
    if not token:
        return content

    rewritten = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if (
            not line
            or line.startswith("#")
            or line.startswith("http://")
            or line.startswith("https://")
        ):
            rewritten.append(raw_line)
            continue

        separator = "&" if "?" in raw_line else "?"
        rewritten.append(f"{raw_line}{separator}{STREAM_QUERY_PARAM}={token}")

    trailing_newline = "\n" if content.endswith("\n") else ""
    return "\n".join(rewritten) + trailing_newline

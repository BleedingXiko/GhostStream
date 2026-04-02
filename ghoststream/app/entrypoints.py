"""Stable runtime factory entrypoints backed by the Specter-first app layer."""

from __future__ import annotations

from ..config import load_config
from .runtime import GhostStreamRuntime


def create_runtime(config=None) -> GhostStreamRuntime:
    return GhostStreamRuntime(config or load_config())

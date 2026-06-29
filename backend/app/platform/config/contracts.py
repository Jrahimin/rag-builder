"""Configuration layer model — precedence only (deployment config is active).

Per-platform and per-Project typed configuration schemas and a resolver are
introduced with the first consumer (Projects module). Deployment settings live
in ``core.config.Settings``.
"""

from __future__ import annotations

from enum import StrEnum


class ConfigLayer(StrEnum):
    """Configuration tiers in ascending priority (lowest first)."""

    DEPLOYMENT = "deployment"
    PLATFORM = "platform"
    PROJECT = "project"


# Later layers override earlier layers for the same key.
CONFIG_PRECEDENCE_ORDER: tuple[ConfigLayer, ...] = (
    ConfigLayer.DEPLOYMENT,
    ConfigLayer.PLATFORM,
    ConfigLayer.PROJECT,
)

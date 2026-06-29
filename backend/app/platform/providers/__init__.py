"""Provider abstraction — errors and capability reference only.

Add concrete ``Protocol`` or ABC interfaces when the first provider ships.
Implementations live in ``implementations/``; vendor SDKs never leave that tree.
"""

from app.platform.providers.contracts import ProviderCapability
from app.platform.providers.errors import (
    ProviderAuthenticationError,
    ProviderConnectionError,
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)

__all__ = [
    "ProviderAuthenticationError",
    "ProviderCapability",
    "ProviderConnectionError",
    "ProviderError",
    "ProviderRateLimitError",
    "ProviderTimeoutError",
    "ProviderUnavailableError",
]

"""Shared Cohere HTTP connection helpers.

Embed and rerank keep feature-specific models and timeouts. Credentials and base
URL come from the shared Cohere config, with legacy reranker env fallbacks.
"""

from __future__ import annotations

import threading
from typing import Any

import httpx

_clients: dict[tuple[str, float], httpx.AsyncClient] = {}
_lock = threading.Lock()


def shared_cohere_client(*, base_url: str, timeout: float) -> httpx.AsyncClient:
    """Reuse one AsyncClient per (base_url, timeout). Embed and rerank keep separate timeouts."""
    key = (base_url.rstrip("/"), timeout)
    with _lock:
        client = _clients.get(key)
        if client is None or client.is_closed:
            client = httpx.AsyncClient(base_url=key[0], timeout=timeout)
            _clients[key] = client
        return client


def clear_shared_cohere_clients() -> None:
    """Drop cached clients. Tests call this after asserting reuse."""
    with _lock:
        _clients.clear()


async def cohere_post(
    *,
    base_url: str,
    path: str,
    api_key: str,
    payload: dict[str, Any],
    request_timeout_seconds: float,
) -> httpx.Response:
    client = shared_cohere_client(base_url=base_url, timeout=request_timeout_seconds)
    return await client.post(
        path,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
    )

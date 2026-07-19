"""Bounded async HTTP implementation for webhook delivery."""

from __future__ import annotations

import time

import httpx

from app.platform.webhooks.contracts import WebhookHttpResponse, WebhookTransport


class HttpWebhookProvider(WebhookTransport):
    async def post(
        self,
        *,
        url: str,
        body: bytes,
        headers: dict[str, str],
        timeout_seconds: float,
    ) -> WebhookHttpResponse:
        started = time.perf_counter()
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
        ) as client:
            response = await client.post(url, content=body, headers=headers)
        return WebhookHttpResponse(
            status_code=response.status_code,
            body=response.text,
            latency_ms=(time.perf_counter() - started) * 1000,
        )

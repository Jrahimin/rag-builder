"""HTTP middleware for per-request observability context.

Assigns (or honors an inbound) ``request_id`` and ``trace_id`` for every
request, binds them to the logging context so all log lines emitted while
handling the request are correlated, and echoes them back as response
headers for end-to-end tracing across services.
"""

from __future__ import annotations

import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging import bind_request_context, clear_request_context, get_logger

log = get_logger("app.request")

REQUEST_ID_HEADER = "X-Request-ID"
TRACE_ID_HEADER = "X-Trace-ID"


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Attach correlation IDs and emit structured access logs."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        trace_id = request.headers.get(TRACE_ID_HEADER) or uuid.uuid4().hex

        request.state.request_id = request_id
        request.state.trace_id = trace_id
        bind_request_context(request_id=request_id, trace_id=trace_id)

        client_host = request.client.host if request.client else None
        start = time.perf_counter()
        log.info(
            "request_started",
            method=request.method,
            path=request.url.path,
            client=client_host,
        )

        try:
            response = await call_next(request)
        except Exception:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            log.exception(
                "request_failed",
                method=request.method,
                path=request.url.path,
                duration_ms=duration_ms,
            )
            raise
        else:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            log.info(
                "request_completed",
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                duration_ms=duration_ms,
            )
            response.headers[REQUEST_ID_HEADER] = request_id
            response.headers[TRACE_ID_HEADER] = trace_id
            return response
        finally:
            clear_request_context()

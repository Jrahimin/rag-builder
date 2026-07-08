"""Global exception handlers producing the standard error envelope.

All errors -- expected (:class:`APEError`), framework (validation, HTTP), or
entirely unexpected -- are funneled through here so clients always receive a
consistent :class:`ErrorResponse`. Stack traces are logged with a
``trace_id`` but never leaked to the client.
"""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.exceptions import APEError, RateLimitError, UnauthorizedError
from app.core.http.envelopes import ErrorDetail, ErrorInfo, ErrorResponse
from app.core.logging import get_logger

log = get_logger(__name__)

# Map common HTTP status codes to stable machine-readable error codes.
_STATUS_CODE_MAP: dict[int, str] = {
    status.HTTP_400_BAD_REQUEST: "bad_request",
    status.HTTP_401_UNAUTHORIZED: "unauthorized",
    status.HTTP_403_FORBIDDEN: "forbidden",
    status.HTTP_404_NOT_FOUND: "not_found",
    status.HTTP_405_METHOD_NOT_ALLOWED: "method_not_allowed",
    status.HTTP_409_CONFLICT: "conflict",
    status.HTTP_422_UNPROCESSABLE_CONTENT: "validation_error",
    status.HTTP_429_TOO_MANY_REQUESTS: "rate_limited",
    status.HTTP_500_INTERNAL_SERVER_ERROR: "internal_error",
    status.HTTP_503_SERVICE_UNAVAILABLE: "service_unavailable",
}


def _trace_id(request: Request) -> str | None:
    return getattr(request.state, "trace_id", None)


def _build_response(
    *,
    request: Request,
    status_code: int,
    code: str,
    message: str,
    details: list[ErrorDetail] | None = None,
) -> JSONResponse:
    payload = ErrorResponse(
        error=ErrorInfo(
            code=code,
            message=message,
            trace_id=_trace_id(request),
            details=details,
        )
    )
    return JSONResponse(status_code=status_code, content=payload.model_dump(exclude_none=True))


async def _handle_ape_error(request: Request, exc: APEError) -> JSONResponse:
    log.warning(
        "application_error",
        code=exc.code,
        status_code=exc.status_code,
        error=exc.message,
        **exc.context,
    )
    response = _build_response(
        request=request,
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
        details=exc.details,
    )
    if isinstance(exc, RateLimitError):
        response.headers["Retry-After"] = str(exc.retry_after_seconds)
    if exc.status_code == status.HTTP_401_UNAUTHORIZED or isinstance(exc, UnauthorizedError):
        response.headers["WWW-Authenticate"] = 'Bearer realm="APE"'
    return response


def _validation_field_path(loc: tuple[object, ...]) -> str:
    """Map a Pydantic error location to a client-friendly field path."""
    parts = [str(part) for part in loc]
    if not parts:
        return ""
    if parts[0] == "body":
        remainder = parts[1:]
        return ".".join(remainder) if remainder else "body"
    return ".".join(parts)


async def _handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    details = [
        ErrorDetail(
            field=_validation_field_path(tuple(error.get("loc", ()))),
            message=error.get("msg", "Invalid value."),
            type=error.get("type"),
        )
        for error in exc.errors()
    ]
    log.info("request_validation_failed", error_count=len(details))
    return _build_response(
        request=request,
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        code="validation_error",
        message="The request failed validation.",
        details=details,
    )


async def _handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = _STATUS_CODE_MAP.get(exc.status_code, "http_error")
    message = exc.detail if isinstance(exc.detail, str) else "An HTTP error occurred."
    return _build_response(
        request=request,
        status_code=exc.status_code,
        code=code,
        message=message,
    )


async def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    # Unknown failure: log the full traceback with the trace_id for correlation,
    # but return an opaque message so internals never leak to the client.
    log.exception("unhandled_exception", error=str(exc))
    return _build_response(
        request=request,
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        code="internal_error",
        message="An unexpected error occurred.",
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Attach all global exception handlers to the application."""
    app.add_exception_handler(APEError, _handle_ape_error)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _handle_validation_error)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, _handle_unexpected_error)

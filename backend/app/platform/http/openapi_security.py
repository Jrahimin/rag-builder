"""OpenAPI security scheme definitions for API-key and browser admin authentication."""

from __future__ import annotations

from fastapi.openapi.utils import get_openapi
from fastapi.security import APIKeyHeader, HTTPBearer

ORG_BEARER_SCHEME = HTTPBearer(
    auto_error=False,
    scheme_name="OrganizationBearer",
    description="Organization API key: `Authorization: Bearer ape_live_…`",
)
ORG_API_KEY_SCHEME = APIKeyHeader(
    name="X-API-Key",
    auto_error=False,
    scheme_name="OrganizationApiKey",
    description="Organization API key via `X-API-Key: ape_live_…`",
)

_ORG_SECURITY: list[dict[str, list[str]]] = [
    {"OrganizationBearer": []},
    {"OrganizationApiKey": []},
]
_ADMIN_SECURITY: list[dict[str, list[str]]] = [{"AdminCookie": []}]


def _apply_route_security(openapi_schema: dict[str, object]) -> None:
    paths: dict[str, object] = openapi_schema.get("paths", {})  # type: ignore[assignment]
    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        is_admin_path = "/organizations" in path or "/operator" in path or path == "/metrics"
        security = _ADMIN_SECURITY if is_admin_path else _ORG_SECURITY
        for method, operation in path_item.items():
            if method.startswith("x-") or not isinstance(operation, dict):
                continue
            if path in ("/health/live", "/health/ready"):
                continue
            operation["security"] = security


def configure_openapi_security(app: object) -> None:
    """Attach security schemes and per-route requirements to the OpenAPI schema."""

    def custom_openapi() -> dict[str, object]:
        if app.openapi_schema:  # type: ignore[attr-defined]
            return app.openapi_schema  # type: ignore[attr-defined]

        openapi_schema = get_openapi(
            title=app.title,  # type: ignore[attr-defined]
            version=app.version,  # type: ignore[attr-defined]
            description=app.description,  # type: ignore[attr-defined]
            routes=app.routes,  # type: ignore[attr-defined]
        )
        components = openapi_schema.setdefault("components", {})
        if isinstance(components, dict):
            components["securitySchemes"] = {
                "OrganizationBearer": {
                    "type": "http",
                    "scheme": "bearer",
                    "description": "Organization API key (`ape_live_…`)",
                },
                "OrganizationApiKey": {
                    "type": "apiKey",
                    "in": "header",
                    "name": "X-API-Key",
                    "description": "Organization API key (`ape_live_…`)",
                },
                "AdminCookie": {
                    "type": "apiKey",
                    "in": "cookie",
                    "name": "ape_admin_access",
                    "description": "HttpOnly Super Admin browser session cookie",
                },
            }
        _apply_route_security(openapi_schema)
        app.openapi_schema = openapi_schema  # type: ignore[attr-defined]
        return openapi_schema

    app.openapi = custom_openapi  # type: ignore[attr-defined]

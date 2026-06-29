"""Composition-root dependency aliases — for ``api/`` routers only."""

from app.dependencies.common import DbSessionDep, HealthServiceDep, SettingsDep

__all__ = [
    "DbSessionDep",
    "HealthServiceDep",
    "SettingsDep",
]

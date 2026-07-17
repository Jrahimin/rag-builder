"""Unit tests for auth configuration validation."""

from __future__ import annotations

import pytest

from app.core.auth_config_validation import AuthConfigurationError, validate_auth_config
from app.core.config import AppConfig, AuthConfig, Environment, Settings

pytestmark = pytest.mark.unit

PEPPER = "integration-test-pepper-32-chars-min"
ADMIN_KEY = "ape_live_admin_integration_test_key_32bytes"


def _settings(*, auth: AuthConfig, env: Environment = Environment.DEVELOPMENT) -> Settings:
    return Settings(app=AppConfig(env=env), auth=auth)


def test_disabled_auth_allowed_in_development() -> None:
    validate_auth_config(_settings(auth=AuthConfig(enabled=False)))


def test_disabled_auth_rejected_in_production() -> None:
    with pytest.raises(AuthConfigurationError, match="APE_AUTH__ENABLED"):
        validate_auth_config(_settings(auth=AuthConfig(enabled=False), env=Environment.PRODUCTION))


def test_enabled_auth_requires_admin_key_and_pepper() -> None:
    with pytest.raises(AuthConfigurationError, match="ADMIN_API_KEY"):
        validate_auth_config(_settings(auth=AuthConfig(enabled=True)))

    with pytest.raises(AuthConfigurationError, match="KEY_PEPPER"):
        validate_auth_config(_settings(auth=AuthConfig(enabled=True, admin_api_key=ADMIN_KEY)))


def test_enabled_auth_rejects_short_secrets() -> None:
    with pytest.raises(AuthConfigurationError, match="ADMIN_API_KEY"):
        validate_auth_config(
            _settings(
                auth=AuthConfig(
                    enabled=True,
                    admin_api_key="short",
                    key_pepper=PEPPER,
                )
            )
        )

    with pytest.raises(AuthConfigurationError, match="KEY_PEPPER"):
        validate_auth_config(
            _settings(
                auth=AuthConfig(
                    enabled=True,
                    admin_api_key=ADMIN_KEY,
                    key_pepper="short",
                )
            )
        )


def test_enabled_auth_accepts_valid_configuration() -> None:
    validate_auth_config(
        _settings(auth=AuthConfig(enabled=True, admin_api_key=ADMIN_KEY, key_pepper=PEPPER))
    )

"""Unit tests for auth configuration validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.auth_config_validation import AuthConfigurationError, validate_auth_config
from app.core.config import AppConfig, AuthConfig, Environment, Settings

pytestmark = pytest.mark.unit

PEPPER = "integration-test-pepper-32-chars-min"
ADMIN_JWT_SECRET = "admin-jwt-secret-for-integration-tests-32"


def _settings(*, auth: AuthConfig, env: Environment = Environment.DEVELOPMENT) -> Settings:
    return Settings(app=AppConfig(env=env), auth=auth)


def test_disabled_auth_allowed_in_development() -> None:
    validate_auth_config(_settings(auth=AuthConfig(enabled=False)))


def test_disabled_auth_rejected_in_production() -> None:
    with pytest.raises(AuthConfigurationError, match="APE_AUTH__ENABLED"):
        validate_auth_config(_settings(auth=AuthConfig(enabled=False), env=Environment.PRODUCTION))


def test_enabled_auth_requires_jwt_secret_and_pepper() -> None:
    with pytest.raises(AuthConfigurationError, match="KEY_PEPPER"):
        validate_auth_config(_settings(auth=AuthConfig(enabled=True)))

    with pytest.raises(AuthConfigurationError, match="ADMIN_JWT_SECRET"):
        validate_auth_config(_settings(auth=AuthConfig(enabled=True, key_pepper=PEPPER)))


def test_enabled_auth_rejects_short_secrets() -> None:
    with pytest.raises(AuthConfigurationError, match="ADMIN_JWT_SECRET"):
        validate_auth_config(
            _settings(
                auth=AuthConfig(
                    enabled=True,
                    key_pepper=PEPPER,
                    admin_jwt_secret="short",
                )
            )
        )

    with pytest.raises(AuthConfigurationError, match="KEY_PEPPER"):
        validate_auth_config(
            _settings(
                auth=AuthConfig(
                    enabled=True,
                    key_pepper="short",
                    admin_jwt_secret=ADMIN_JWT_SECRET,
                )
            )
        )


def test_enabled_auth_accepts_valid_configuration() -> None:
    validate_auth_config(
        _settings(
            auth=AuthConfig(
                enabled=True,
                key_pepper=PEPPER,
                admin_jwt_secret=ADMIN_JWT_SECRET,
            )
        )
    )


def test_access_token_expiry_allows_eight_hours_but_not_more() -> None:
    config = AuthConfig(admin_access_token_expire_minutes=8 * 60)
    assert config.admin_access_token_expire_minutes == 8 * 60
    with pytest.raises(ValidationError):
        AuthConfig(admin_access_token_expire_minutes=(8 * 60) + 1)

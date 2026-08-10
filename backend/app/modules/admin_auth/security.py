"""Password, token, and JWT primitives for human Super Admin authentication."""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import AuthConfig

_password_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, password)
    except (InvalidHashError, VerificationError, VerifyMismatchError):
        return False


def generate_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def create_access_token(*, admin_id: UUID, email: str, session_id: UUID, config: AuthConfig) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(admin_id),
            "email": email,
            "role": "SUPER_ADMIN",
            "sid": str(session_id),
            "type": "admin_access",
            "iat": now,
            "exp": now + timedelta(minutes=config.admin_access_token_expire_minutes),
        },
        config.admin_jwt_secret,
        algorithm="HS256",
    )


def decode_access_token(token: str, *, config: AuthConfig) -> dict[str, object]:
    try:
        payload = jwt.decode(token, config.admin_jwt_secret, algorithms=["HS256"])
    except jwt.InvalidTokenError as exc:
        raise ValueError("Invalid access token") from exc
    if payload.get("type") != "admin_access" or not payload.get("sub") or not payload.get("sid"):
        raise ValueError("Invalid access token")
    return payload

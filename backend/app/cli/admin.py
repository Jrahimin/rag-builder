"""Safe, explicit Super Admin account commands.

Run after migrations: ``python -m app.cli.admin create --email owner@example.com``.
This creates a SUPER_ADMIN. Additional operators created in the console are ADMIN.
To replace a forgotten password, use ``set-password``. The password is prompted
so it never appears in shell history or process lists.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass

from app.core.config import get_settings
from app.models.admin_user import AdminRole, AdminUser
from app.modules.admin_auth.repository import AdminAuthRepository
from app.modules.admin_auth.security import hash_password
from app.platform.db.session import Database


async def _create(email: str, password: str) -> int:
    database = Database(get_settings())
    try:
        async with database.session_factory() as session:
            repository = AdminAuthRepository(session)
            normalized_email = email.strip().lower()
            if await repository.get_admin_by_email(normalized_email):
                raise ValueError("An admin with this email already exists.")
            repository.add(
                AdminUser(
                    email=normalized_email,
                    password_hash=hash_password(password),
                    role=AdminRole.SUPER_ADMIN.value,
                    is_active=True,
                )
            )
            await repository.commit()
        return 0
    finally:
        await database.dispose()


async def _set_password(email: str, password: str) -> int:
    database = Database(get_settings())
    try:
        async with database.session_factory() as session:
            repository = AdminAuthRepository(session)
            normalized_email = email.strip().lower()
            admin = await repository.get_admin_by_email(normalized_email)
            if admin is None:
                raise ValueError("No Super Admin exists for that email.")
            admin.password_hash = hash_password(password)
            await repository.revoke_sessions_for_admin(admin.id)
            await repository.commit()
        return 0
    finally:
        await database.dispose()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli.admin")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="Create the initial Super Admin")
    create.add_argument("--email", required=True)
    set_password = commands.add_parser("set-password", help="Replace a Super Admin password")
    set_password.add_argument("--email", required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    password = getpass.getpass("Password: ")
    confirmation = getpass.getpass("Confirm password: ")
    if len(password) < 8:
        raise SystemExit("Password must be at least 8 characters.")
    if password != confirmation:
        raise SystemExit("Passwords do not match.")
    try:
        if args.command == "create":
            return asyncio.run(_create(args.email, password))
        return asyncio.run(_set_password(args.email, password))
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    raise SystemExit(main())

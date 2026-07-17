"""Characterization tests for shared Project-scoped lifecycle queries."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from app.modules.conversations.repositories.conversation_repository import (
    ConversationRepository,
)
from app.modules.knowledge.repositories.document_repository import DocumentRepository
from app.platform.persistence.filters import LifecycleListFilters

pytestmark = pytest.mark.unit


def _sql(statement: object) -> str:
    return str(
        statement.compile(  # type: ignore[attr-defined]
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


async def test_get_by_id_is_project_scoped_and_hides_deleted_rows() -> None:
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session.execute.return_value = result
    repository = DocumentRepository(session, uuid.uuid4())

    await repository.get_by_id(uuid.uuid4())

    statement = session.execute.await_args.args[0]
    sql = _sql(statement)
    assert "documents.project_id" in sql
    assert "documents.id" in sql
    assert "documents.deleted_at IS NULL" in sql


async def test_list_page_keeps_scope_and_lifecycle_filters_in_shared_base() -> None:
    session = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    session.execute.return_value = result
    repository = ConversationRepository(session, uuid.uuid4())

    await repository.list_page(
        limit=25,
        offset=5,
        filters=LifecycleListFilters(include_deleted=False, is_active=True),
    )

    statement = session.execute.await_args.args[0]
    sql = _sql(statement)
    assert "conversations.project_id" in sql
    assert "conversations.deleted_at IS NULL" in sql
    assert "conversations.is_active" in sql
    assert "true" in sql
    assert "conversations.last_message_at DESC NULLS LAST" in sql

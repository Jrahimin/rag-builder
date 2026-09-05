"""Message repository history-boundary tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from app.modules.conversations.repositories.message_repository import MessageRepository

pytestmark = pytest.mark.unit


def _sql(statement: object) -> str:
    return str(
        statement.compile(  # type: ignore[attr-defined]
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


async def test_list_recent_excludes_current_message_with_created_at_id_boundary() -> None:
    session = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    session.execute.return_value = result
    repository = MessageRepository(session, uuid.uuid4())
    before_id = uuid.uuid4()
    before_created_at = datetime(2026, 8, 1, tzinfo=UTC)

    await repository.list_recent_for_conversation(
        uuid.uuid4(),
        limit=8,
        before_created_at=before_created_at,
        before_id=before_id,
    )

    sql = _sql(session.execute.await_args.args[0])
    assert "messages.created_at" in sql
    assert "messages.id" in sql
    assert str(before_id) in sql
    assert "2026-08-01" in sql
    assert "LIMIT 8" in sql


@pytest.mark.parametrize("field", ["before_id", "before_created_at"])
async def test_partial_history_boundary_is_rejected(field: str) -> None:
    session = AsyncMock()
    repository = MessageRepository(session, uuid.uuid4())
    value = uuid.uuid4() if field == "before_id" else datetime(2026, 8, 1, tzinfo=UTC)
    with pytest.raises(ValueError, match="both"):
        await repository.list_recent_for_conversation(uuid.uuid4(), limit=8, **{field: value})
    session.execute.assert_not_awaited()

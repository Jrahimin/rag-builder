"""Neighbour discovery must stay in the exact Project/build/processing version."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from app.modules.retrieval.repositories.retrieval_chunk_repository import RetrievalChunkRepository

pytestmark = pytest.mark.unit


async def test_adjacent_sql_preserves_scope_and_bounds_page_discovery():
    session = AsyncMock()
    session.execute.return_value = MagicMock()
    session.execute.return_value.scalars.return_value.all.return_value = []
    project, build = uuid.uuid4(), uuid.uuid4()
    anchors = [uuid.uuid4() for _ in range(6)]
    await RetrievalChunkRepository(session, project).adjacent_ids(anchors, index_build_id=build)
    stmt = session.execute.call_args.args[0]
    sql = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert str(project) in sql
    assert sql.count(str(build)) == 2  # both anchor and neighbour indexed in this build
    assert "document_version = document_chunks.document_version" in sql
    assert "coalesce(document_chunks.page_start, document_chunks.page_number)" in sql
    assert "chunk_index BETWEEN" in sql  # fallback for unpaged text
    assert "NOT IN" in sql  # repeated anchors cannot crowd out adjacent text
    assert all(str(anchor) in sql for anchor in anchors[:4])
    assert all(str(anchor) not in sql for anchor in anchors[4:])
    assert "LIMIT 48" in sql


async def test_empty_anchors_do_not_query_or_broaden_scope():
    session = AsyncMock()
    result = await RetrievalChunkRepository(session, uuid.uuid4()).adjacent_ids(
        [], index_build_id=uuid.uuid4()
    )
    assert result == ()
    session.execute.assert_not_called()

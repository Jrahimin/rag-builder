"""Neighbour discovery must stay in the exact Project/build/processing version."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from app.modules.retrieval.repositories.retrieval_chunk_repository import RetrievalChunkRepository

pytestmark = pytest.mark.unit


def _row(**values):
    return SimpleNamespace(**values)


async def test_preceding_page_is_in_same_build_document_version_and_generation():
    session = AsyncMock()
    project, build, anchor = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    rows = MagicMock()
    rows.tuples.return_value.all.return_value = [(anchor, "AY 2030-31")]
    session.execute.return_value = rows
    found = await RetrievalChunkRepository(session, project).preceding_indexed_text(
        [anchor], index_build_id=build
    )
    assert found == {anchor: "AY 2030-31"}
    sql = str(
        session.execute.call_args.args[0].compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert str(project) in sql and str(anchor) in sql
    assert sql.count(str(build)) == 2
    assert "document_version" in sql and "generation_id IS NOT DISTINCT FROM" in sql
    assert "chunk_index - 1" in sql and "page_number) - 1" in sql
    assert "coalesce(document_chunks_2.page_end, document_chunks_2.page_number) BETWEEN" in sql
    assert "coalesce(document_chunks_2.page_start, document_chunks_2.page_number) <" in sql


async def test_adjacent_sql_preserves_scope_and_bounds_page_discovery():
    session = AsyncMock()
    project, build = uuid.uuid4(), uuid.uuid4()
    document = uuid.uuid4()
    anchors = [uuid.uuid4() for _ in range(6)]
    anchor_row = _row(
        id=anchors[0],
        document_id=document,
        document_version=3,
        chunk_index=49,
        page_start=1,
        page_end=1,
        page_number=1,
        chunk_metadata={"strategy_used": "markdown"},
    )
    first = MagicMock()
    first.all.return_value = [anchor_row]
    second = MagicMock()
    second.all.return_value = []
    session.execute.side_effect = [first, second]
    await RetrievalChunkRepository(session, project).adjacent_ids(anchors, index_build_id=build)
    anchor_sql = str(
        session.execute.call_args_list[0]
        .args[0]
        .compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )
    candidate_sql = str(
        session.execute.call_args_list[1]
        .args[0]
        .compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )
    assert str(project) in anchor_sql
    assert str(build) in anchor_sql
    assert all(str(anchor) in anchor_sql for anchor in anchors[:4])
    assert all(str(anchor) not in anchor_sql for anchor in anchors[4:])
    assert str(project) in candidate_sql
    assert str(build) in candidate_sql
    assert str(document) in candidate_sql
    assert "document_version" in candidate_sql
    assert "NOT IN" in candidate_sql
    assert "BETWEEN" in candidate_sql
    assert str(anchors[0]) in candidate_sql


async def test_empty_anchors_do_not_query_or_broaden_scope():
    session = AsyncMock()
    result = await RetrievalChunkRepository(session, uuid.uuid4()).adjacent_ids(
        [], index_build_id=uuid.uuid4()
    )
    assert result == ()
    session.execute.assert_not_called()


async def test_inactive_anchor_outside_the_build_does_not_query_the_corpus():
    session = AsyncMock()
    first = MagicMock()
    first.all.return_value = []
    session.execute.return_value = first
    result = await RetrievalChunkRepository(session, uuid.uuid4()).adjacent_ids(
        [uuid.uuid4()], index_build_id=uuid.uuid4()
    )
    assert result == ()
    assert session.execute.await_count == 1


async def test_empty_indexed_identities_do_not_query_the_corpus():
    session = AsyncMock()
    found = await RetrievalChunkRepository(session, uuid.uuid4()).map_indexed_identities(
        [],
        index_build_id=uuid.uuid4(),
    )
    assert found == {}
    session.execute.assert_not_called()


async def test_indexed_identity_sql_stays_inside_project_build_and_optional_document():
    session = AsyncMock()
    project, build, document = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    chunk_id = uuid.uuid4()
    result = MagicMock()
    result.all.return_value = [
        _row(id=chunk_id, metadata_snapshot={"region": "Dhaka"}, _mapping={})
    ]
    session.execute.return_value = result
    found = await RetrievalChunkRepository(session, project).map_indexed_identities(
        [chunk_id, chunk_id],
        index_build_id=build,
        document_id=document,
        metadata_filter={"region": "Dhaka"},
    )
    sql = str(
        session.execute.call_args.args[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert found == {chunk_id: {"region": "Dhaka"}}
    assert str(project) in sql
    assert str(build) in sql
    assert str(document) in sql
    assert str(chunk_id) in sql
    assert "deleted_at" in sql
    session.execute.assert_awaited_once()

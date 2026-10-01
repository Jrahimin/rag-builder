"""Keyword candidate SQL uses a typed regconfig and tokenizer-key overlap."""

from __future__ import annotations

import pytest
from sqlalchemy import column
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR

from app.modules.retrieval.keyword.fts import keyword_candidate_predicate, to_search_vector

pytestmark = pytest.mark.unit


def test_to_search_vector_casts_regconfig() -> None:
    sql = str(
        to_search_vector("simple", column("content_normalized")).compile(
            dialect=postgresql.dialect()
        )
    )
    assert "to_tsvector" in sql
    assert "CAST" in sql.upper()
    assert "REGCONFIG" in sql.upper()


def test_keyword_candidates_union_fts_and_term_keys() -> None:
    sql = str(
        keyword_candidate_predicate(
            column("search_vector", TSVECTOR),
            column("term_frequencies", JSONB),
            regconfig="simple",
            query="উৎসে কর",
            query_terms=["উৎসে", "কর"],
        ).compile(dialect=postgresql.dialect())
    )
    assert "plainto_tsquery" in sql
    assert "@@" in sql
    assert "?|" in sql
    assert "CAST" in sql.upper()
    assert "REGCONFIG" in sql.upper()


@pytest.mark.asyncio
async def test_bm25_is_sql_ordering_before_limit_for_long_mixed_query():
    import uuid
    from unittest.mock import AsyncMock, MagicMock

    from app.modules.retrieval.repositories.chunk_keyword_index_repository import (
        ChunkKeywordIndexRepository,
    )

    result = MagicMock()
    result.all.return_value = []
    session = AsyncMock()
    session.execute.return_value = result
    repository = ChunkKeywordIndexRepository(session, uuid.uuid4())
    await repository.search_candidates(
        query="office rent ভাড়া কর্তন source rule rate payer",
        index_build_id=uuid.uuid4(),
        embedding_set_version=1,
        top_k=5,
    )
    sql = str(session.execute.call_args.args[0].compile(dialect=postgresql.dialect()))
    assert "ln(" in sql and "keyword_term_stats" in sql
    assert "ts_rank_cd" not in sql
    assert sql.index("ORDER BY") < sql.index("LIMIT")
    assert "chunk_keyword_index.chunk_id" in sql[sql.index("ORDER BY") :]

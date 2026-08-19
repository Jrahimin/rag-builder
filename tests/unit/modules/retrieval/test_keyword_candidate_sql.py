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

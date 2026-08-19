"""PostgreSQL FTS helpers that bind ``regconfig`` correctly.

SQLAlchemy otherwise sends the config name as VARCHAR, which does not match
``to_tsvector(regconfig, text)`` / ``plainto_tsquery(regconfig, text)``.
"""

from __future__ import annotations

from sqlalchemy import ColumnElement, cast, func, literal, or_
from sqlalchemy.dialects.postgresql import ARRAY, REGCONFIG, TEXT

_MAX_QUERY_TERMS = 256


def fts_config(name: str) -> ColumnElement[object]:
    return cast(literal(name), REGCONFIG)


def to_search_vector(regconfig: str, document: object) -> ColumnElement[object]:
    return func.to_tsvector(fts_config(regconfig), document)


def plain_query(regconfig: str, query: str) -> ColumnElement[object]:
    return func.plainto_tsquery(fts_config(regconfig), query)


def keyword_candidate_predicate(
    search_vector: ColumnElement[object],
    term_frequencies: ColumnElement[object],
    *,
    regconfig: str,
    query: str,
    query_terms: list[str],
) -> ColumnElement[bool]:
    """FTS match or JSONB key overlap with the Python BM25 tokenizer."""
    ts_query = plain_query(regconfig, query)
    fts_match = search_vector.op("@@")(ts_query)
    terms = list(dict.fromkeys(query_terms))[:_MAX_QUERY_TERMS]
    if not terms:
        return fts_match
    lexical_overlap = term_frequencies.has_any(cast(terms, ARRAY(TEXT)))
    return or_(fts_match, lexical_overlap)

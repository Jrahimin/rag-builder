"""Bounded source graph discovery preserves unresolved frontier identity."""

import uuid
from unittest.mock import AsyncMock

import pytest

from app.composition.source_metadata import KnowledgeRetrievalSourceMetadataAdapter
from app.modules.knowledge.source_metadata_read import KnowledgeIncomingModifier
from app.modules.retrieval.retrievers.hybrid_retriever import _expansion_diagnostics

pytestmark = pytest.mark.unit


def edge(base, modifier):
    return KnowledgeIncomingModifier(
        relationship_id=uuid.uuid4(),
        base_revision_id=base,
        base_document_id=uuid.uuid4(),
        modifier_revision_id=modifier,
        modifier_document_id=uuid.uuid4(),
        modifier_effective_from="2026-07-01",
        modifier_effective_to=None,
        modifier_published_date="2026-08-01",
        modifier_revision_number=1,
        base_effective_from=None,
        base_effective_to=None,
        outcome="expanded",
        scope_hash="a" * 64,
        applicable=True,
    )


async def test_two_hops_and_frontier_probe_never_claim_complete_authority():
    ids = [uuid.uuid4() for _ in range(4)]
    adapter = KnowledgeRetrievalSourceMetadataAdapter(AsyncMock())
    adapter._reader.incoming_modifiers = AsyncMock(
        side_effect=[[edge(ids[i], ids[i + 1])] for i in range(3)]
    )
    records = await adapter.incoming_modifiers(
        project_id=uuid.uuid4(),
        base_revision_ids=(ids[0],),
        generation=7,
        as_of=None,
        index_build_id=uuid.uuid4(),
    )
    assert [r.depth for r in records] == [1, 2, 3]
    assert records[-1].outcome.value == "depth_limit"
    diagnostics = _expansion_diagnostics(status="expanded", records=records)
    assert diagnostics["relationship_discovery_status"] == "truncated"
    assert str(ids[-1]) in str(diagnostics["unresolved_frontier_ids"])


async def test_source_ceiling_retains_excluded_identity():
    root = uuid.uuid4()
    rows = [edge(root, uuid.uuid4()) for _ in range(9)]
    adapter = KnowledgeRetrievalSourceMetadataAdapter(AsyncMock())
    adapter._reader.incoming_modifiers = AsyncMock(side_effect=[rows, []])
    records = await adapter.incoming_modifiers(
        project_id=uuid.uuid4(),
        base_revision_ids=(root,),
        generation=7,
        as_of=None,
        index_build_id=uuid.uuid4(),
    )
    assert sum(r.outcome.value == "expanded" for r in records) == 8
    assert records[-1].outcome.value == "source_cap_exceeded"
    assert (
        _expansion_diagnostics(status="expanded", records=records)["relationship_discovery_status"]
        == "truncated"
    )

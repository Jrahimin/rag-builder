"""Private generation must not alter current source version/status or generation."""

import json
import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from app.core.config import ChunkingConfig
from app.models.document import Document, DocumentStatus
from app.modules.knowledge.services.chunking.models import ChunkingStrategy
from app.modules.knowledge.services.chunking_service import ChunkingService
from app.modules.knowledge.services.private_structural_generation import PrivateStructuralGeneration
from app.platform.providers.contracts.document_parser import ParsedDocument


@pytest.mark.asyncio
async def test_private_generation_keeps_active_document_immutable():
    doc = Document(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        filename="proof.txt",
        storage_key="raw",
        content_sha256="hash",
        size_bytes=30,
        version=7,
        status=DocumentStatus.READY,
    )
    original_generation = uuid.uuid4()
    doc.chunk_generation_id = original_generation
    parsed = ParsedDocument(
        text="Tenants submit a lease.", page_count=1, parser_name="text", parser_version="1"
    )

    class Storage:
        async def get(self, key):
            assert key.endswith("/parsed/v7.json")
            yield json.dumps(parsed.to_dict()).encode()

    session = AsyncMock()
    session.add_all = lambda rows: None
    build_id = uuid.uuid4()
    service = PrivateStructuralGeneration(
        session,
        Storage(),
        ChunkingService(config=ChunkingConfig(strategy=ChunkingStrategy.RECURSIVE_FALLBACK)),
    )
    rows = await service.prepare(doc, build_id)
    assert rows and all(row.generation_id == build_id and row.document_version == 7 for row in rows)
    assert (doc.version, doc.status, doc.chunk_generation_id) == (
        7,
        DocumentStatus.READY,
        original_generation,
    )
    assert rows[0].chunk_metadata["structure_version"] == "structure.v1"
    cleanup = str(
        session.execute.call_args.args[0].compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert str(build_id) in cleanup and "generation_id" in cleanup
    assert str(original_generation) not in cleanup

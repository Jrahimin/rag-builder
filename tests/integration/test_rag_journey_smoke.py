"""Small real PostgreSQL/pgvector smoke for the production-path journey harness."""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path
from typing import ClassVar

import pytest
from sqlalchemy import func, select

from app.cli.rag_journey import DEFAULT_FIXTURE, JourneyOptions, run_journey
from app.core.config import Settings, get_settings
from app.models.document import Document
from app.models.project import Project
from app.models.source_metadata import SourceMetadataRevision
from app.platform.db.session import Database
from app.platform.providers.contracts.embedding import (
    BaseEmbeddingProvider,
    EmbeddingBatchResult,
    EmbeddingPurpose,
)
from app.platform.providers.implementations.storage_factory import create_storage_provider

pytestmark = pytest.mark.integration


class FixtureConceptEmbeddingProvider(BaseEmbeddingProvider):
    """Deterministic semantic fixture embedder for production pgvector smoke tests.

    Each tax concept occupies a stable orthogonal coordinate. Multi-section
    fixture chunks may activate more than one coordinate so bilingual and
    mixed-source queries still retrieve the matching family. The lunar query
    occupies a separate coordinate, so production evidence thresholds remain
    unchanged and still reject unrelated evidence.
    """

    _CONCEPTS: ClassVar[dict[str, int]] = {
        "eligible": 0,
        "rebate": 1,
        "threshold": 2,
        "source_tax": 3,
        "evidence": 4,
        "declared": 5,
        "unknown": 6,
        "other_document": 7,
        "procedure": 8,
    }

    def __init__(self, dimensions: int) -> None:
        self._dimensions = dimensions

    @property
    def provider_name(self) -> str:
        return "fixture-concept"

    @property
    def model_name(self) -> str:
        return "tax-v1-smoke-v1"

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def provider_version(self) -> str:
        return "1"

    @classmethod
    def _concepts(cls, text: str) -> set[str]:
        value = text.casefold()
        if "lunar" in value or "moon colony" in value:
            return {"unknown"}
        found: set[str] = set()
        if ("section 20" in value or "ধারা ২০" in value) and (
            "eligible" in value or "investment" in value or "যোগ্য" in value or "বিনিয়োগ" in value
        ):
            found.add("eligible")
        if "eligible investment" in value or "rebate" in value or "রিবেট" in value:
            found.add("rebate")
        if "tax-free threshold" in value or "tax free threshold" in value or "করমুক্ত" in value:
            found.add("threshold")
        if (
            "source tax" in value
            or "savings-certificate" in value
            or "উৎসে কর" in value
            or "সঞ্চয়পত্রের মুনাফা" in value
        ):
            found.add("source_tax")
        if "certificate বা statement" in value or "প্রয়োজনীয় প্রমাণ" in value or "সহায়ক নথি" in value:
            found.add("evidence")
        if "৭৫০০০" in value.replace(",", "") or "ঘোষিত বিনিয়োগ" in value:
            found.add("declared")
        if (
            "verification reference" in value
            or "review window" in value
            or "calendar days" in value
            or "যাচাইয়ের" in value
            or "স্বতন্ত্র রেফারেন্স" in value
            or "সময়সীমা" in value
        ):
            found.add("procedure")
        return found or {"unknown"}

    async def embed_texts(
        self,
        texts: list[str],
        *,
        purpose: EmbeddingPurpose = EmbeddingPurpose.DOCUMENT,
    ) -> EmbeddingBatchResult:
        vectors: list[list[float]] = []
        for text in texts:
            vector = [0.0] * self._dimensions
            concepts = self._concepts(text)
            if purpose is EmbeddingPurpose.DOCUMENT:
                concepts = {
                    "other_document" if concept == "unknown" else concept for concept in concepts
                }
            for concept in concepts:
                vector[self._CONCEPTS[concept]] = 1.0
            vectors.append(vector)
        return EmbeddingBatchResult(
            vectors=vectors,
            provider=self.provider_name,
            model=self.model_name,
            dimensions=self.dimensions,
            provider_version=self.provider_version,
        )


def _fixture_embedder(
    settings: Settings,
    *,
    dimensions: int | None = None,
    **_: object,
) -> BaseEmbeddingProvider:
    return FixtureConceptEmbeddingProvider(dimensions or settings.embedding.dimensions)


def _install_fixture_embedder(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace only provider construction; services, jobs, and pgvector stay real."""
    for target in (
        "app.platform.providers.implementations.embedding_factory.create_embedding_provider",
        "app.platform.providers.implementations.embedding_factory.create_embedding_provider_for_identity",
        "app.dependencies.retrieval.create_embedding_provider_for_identity",
        "app.worker.handlers.document.create_embedding_provider",
        "app.worker.handlers.corpus.create_embedding_provider",
    ):
        monkeypatch.setattr(target, _fixture_embedder)


async def test_tax_journey_subset_uses_production_diagnostics_and_cleans_up(
    require_postgres: None,
    apply_migrations: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fixture_root = tmp_path / "tax_v1"
    fixture_root.mkdir()
    shutil.copytree(DEFAULT_FIXTURE.parent / "corpus", fixture_root / "corpus")
    payload = json.loads(DEFAULT_FIXTURE.read_text(encoding="utf-8"))
    selected = {
        "eligible_investments_scoped",
        "historical_rebate_rate",
        "hard_document_scope_authority",
        "unknown_lunar_rule",
    }
    payload["cases"] = [case for case in payload["cases"] if case["key"] in selected]
    fixture = fixture_root / "journey.json"
    fixture.write_text(json.dumps(payload), encoding="utf-8")

    monkeypatch.setenv("APE_JOBS__BACKEND", "inline")
    monkeypatch.setenv("APE_JOBS__DISPATCHER_ENABLED", "false")
    get_settings.cache_clear()
    settings = get_settings()
    _install_fixture_embedder(monkeypatch)
    try:
        result, artifact_dir = await run_journey(
            settings,
            JourneyOptions(
                fixture=fixture,
                artifact_root=tmp_path / "artifacts",
                configured_job_backend="inline",
            ),
        )
    finally:
        get_settings.cache_clear()

    assert "setup_error" not in result
    assert result["cleanup"]["status"] == "succeeded"
    assert result["index"]["document_count"] == 6
    structured_anchor_keys = {
        item["key"] for item in payload["anchors"] if item.get("section")
    }
    for key, chunk_ids in result["anchor_mappings"].items():
        assert chunk_ids
        if key in structured_anchor_keys:
            assert len(chunk_ids) == 1, key
    finance_2026_targets = {
        item["source_key"]: item["target_provisions"]
        for item in result["sources"]["finance_2026"]["modifies"]
    }
    assert set(finance_2026_targets) == {"tax_2023", "tax_2023_bn"}
    assert finance_2026_targets["tax_2023_bn"] == [
        "ধারা ১০ — করমুক্ত সীমা",
        "ধারা ২১ — বিনিয়োগ রিবেটের হার",
        "ধারা \u09ea\u09e6 — ব্যক্তিগত করের উদাহরণ",
    ]
    finance_2027_modifies = result["sources"]["finance_2027"]["modifies"]
    assert [item["source_key"] for item in finance_2027_modifies] == ["finance_2026"]
    assert (
        "Section 10 — Revised Tax-Free Threshold"
        not in finance_2027_modifies[0]["target_provisions"]
    )
    assert (artifact_dir / "summary.md").is_file()
    assert (artifact_dir / "results.json").is_file()

    cases = {case["key"]: case for case in result["variants"][0]["cases"]}
    indexed = cases["eligible_investments_scoped"]
    assert indexed["evidence_gate"]["sufficient"] is True
    assert indexed["admitted"]
    assert indexed["fallback"] == {"status": "not_requested", "fallback_used": False}

    historical = cases["historical_rebate_rate"]
    assert historical["fallback"]["fallback_used"] is False
    assert historical["expected"]["as_of"].startswith("2024-01-01")
    effective_config = result["variants"][0]["effective_config"].get("configuration", {})
    source_policy_mode = effective_config.get("source_policy_mode")
    historical_authority_failures = [
        failure for failure in historical["failures"] if failure["stage"] == "authority"
    ]
    if source_policy_mode == "enforce":
        assert historical_authority_failures == []

    scoped = cases["hard_document_scope_authority"]
    old_document_id = result["sources"]["tax_2023"]["document_id"]
    observed_document_ids = {
        item["document_id"]
        for stage in ("candidates", "selected")
        for item in scoped["retrieval"][stage]
        if item.get("document_id")
    }
    observed_document_ids.update(
        item["document_id"] for item in scoped["admitted"] if item.get("document_id")
    )
    assert observed_document_ids <= {old_document_id}
    scoped_anchor_ids = set(result["anchor_mappings"]["rebate_rate_2023"])
    retrieved_ids = {
        item["chunk_id"] for item in scoped["retrieval"]["selected"] if item.get("chunk_id")
    }
    assert scoped_anchor_ids & retrieved_ids
    assert scoped["fallback"]["fallback_used"] is False
    assert scoped["fallback"]["status"] in {"not_requested", "suppressed_scoped_request"}
    expansion_mode = effective_config.get("retrieval", {}).get("modifies_expansion_mode")
    if expansion_mode == "expand":
        assert scoped["authority"]["status"] == "suppressed_document_scope"

    unknown = cases["unknown_lunar_rule"]
    assert unknown["evidence_gate"]["sufficient"] is False
    assert unknown["admitted"] == []
    assert unknown["insufficient_evidence_reason"] is not None
    assert unknown["evidence_gate"]["generation_ran"] is False
    assert unknown["fallback"]["status"] == "not_requested"

    # The runner's relationship-aware purge must leave no aggregate, source,
    # document, or object-prefix artefact for its exact temporary project.
    project_id = uuid.UUID(result["project_id"])
    database = Database(settings)
    try:
        async with database.session_factory() as session:
            assert await session.get(Project, project_id) is None
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(Document)
                    .where(Document.project_id == project_id)
                )
                == 0
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(SourceMetadataRevision)
                    .where(SourceMetadataRevision.project_id == project_id)
                )
                == 0
            )
        assert await create_storage_provider(settings).list_keys(f"{project_id}/") == []
    finally:
        await database.dispose()

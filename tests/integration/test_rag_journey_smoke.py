"""Small real PostgreSQL/pgvector smoke for the production-path journey harness."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from app.cli.rag_journey import DEFAULT_FIXTURE, JourneyOptions, run_journey
from app.core.config import get_settings

pytestmark = pytest.mark.integration


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
    assert result["index"]["document_count"] == 2
    assert all(len(chunk_ids) == 1 for chunk_ids in result["anchor_mappings"].values())
    assert result["sources"]["finance_2026"]["modifies"] == [
        {
            "source_key": "tax_2023",
            "target_revision_id": result["sources"]["tax_2023"]["source_revision_id"],
        }
    ]
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
    assert all(failure["stage"] != "authority" for failure in historical["failures"])

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
    assert scoped["authority"]["status"] == "suppressed_document_scope"

    unknown = cases["unknown_lunar_rule"]
    assert unknown["evidence_gate"]["sufficient"] is False
    assert unknown["admitted"] == []
    assert "status" in unknown["fallback"]

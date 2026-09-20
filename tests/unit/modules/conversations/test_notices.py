"""Unit tests for system-rendered chat notices."""

from __future__ import annotations

import pytest

from app.modules.conversations.notices import (
    NoticeKind,
    insufficient_evidence_notice,
    scope_excludes_effective_modifier_notice,
    verification_failed_notice,
    web_evidence_used_notice,
)

pytestmark = pytest.mark.unit


def test_scope_notice_is_system_metadata_not_a_claim() -> None:
    notice = scope_excludes_effective_modifier_notice(
        language="en",
        modifier_records=[
            {
                "modifier_document_id": "doc-1",
                "modifier_revision_id": "rev-1",
                "modifier_effective_from": "2026-07-01",
                "target_provisions": ["Section 21"],
            }
        ],
    )
    assert notice.kind == NoticeKind.SCOPE_EXCLUDES_EFFECTIVE_MODIFIER
    assert notice.source["modifier_document_id"] == "doc-1"
    assert notice.source["target_provisions"] == ["Section 21"]
    assert "document scope" in notice.text.lower()


def test_bangla_scope_notice_uses_bangla_registry_text() -> None:
    notice = scope_excludes_effective_modifier_notice(language="bn", modifier_records=[])
    assert notice.language == "bn"
    assert "document scope" in notice.text or "document" in notice.text


def test_web_and_insufficient_notices_are_purpose_specific() -> None:
    web = web_evidence_used_notice(language="en")
    missing = insufficient_evidence_notice(language="en")
    assert web.kind == NoticeKind.WEB_EVIDENCE_USED
    assert missing.kind == NoticeKind.INSUFFICIENT_EVIDENCE
    assert web.source == {}
    assert missing.source == {}


def test_verification_failure_notice_does_not_claim_sources_are_missing() -> None:
    notice = verification_failed_notice(language="en")
    assert notice.kind == NoticeKind.VERIFICATION_FAILED
    assert notice.source == {"failure_stage": "coverage_review"}
    assert "verification step failed" in notice.text
    assert "information is absent" in notice.text

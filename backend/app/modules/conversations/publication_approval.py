"""Immutable private publication identity emitted by each verification batch."""

import json
from dataclasses import dataclass
from typing import Any

from app.platform.domain.content_hash import content_hash


def publication_binding(claim: dict[str, Any], display: str, assertion: str) -> str:
    """Citation numbering is presentation; exact proof, meaning and scope are not."""
    evidence = [
        {key: value for key, value in proof.items() if key != "citation_index"}
        for proof in claim.get("evidence", [])
    ]
    return content_hash(
        json.dumps(
            {
                "assertion_id": claim.get("assertion_id"),
                "display": display,
                "assertion": assertion,
                "requirements": claim.get("requirement_ids", []),
                "calculations": claim.get("calculation_references", []),
                "kind": claim.get("claim_kind"),
                "method": claim.get("verification_method"),
                "arithmetic": claim.get("arithmetic_verification"),
                "evidence": evidence,
            },
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )
    )


@dataclass(frozen=True, slots=True)
class PublicationApproval:
    binding: str
    context: str | None
    supported: bool
    complete: bool | None

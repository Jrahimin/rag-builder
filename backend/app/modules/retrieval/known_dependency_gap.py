"""Authoritative missing-index dependency, never inferred from search misses."""

from typing import Any


def known_dependency_gap(diagnostics: dict[str, Any], project_id: object) -> dict[str, Any]:
    if diagnostics.get("source_policy_effective_mode") != "enforce":
        return {}
    if not diagnostics.get("index_build_id") or not diagnostics.get("source_metadata_generation"):
        return {}
    missing = [
        record
        for record in diagnostics.get("modifies_expansion_records", [])
        if record.get("outcome") == "not_in_active_index"
        and record.get("relationship_type") == "modifies"
        and record.get("relationship_id")
        and record.get("modifier_revision_id")
        and record.get("target_provisions")
    ]
    if not missing or project_id is None:
        return {}
    return {
        "known_corpus_gap": True,
        "known_corpus_gap_requirements": [
            "Indexed governing amendment "
            + str(record["modifier_revision_id"])
            + " for "
            + ", ".join(str(value) for value in record["target_provisions"])
            for record in missing
        ],
        "known_corpus_gap_binding": {
            "producer": "source_metadata_dependency.v1",
            "project_id": str(project_id),
            "index_build_id": str(diagnostics["index_build_id"]),
            "source_metadata_generation": diagnostics["source_metadata_generation"],
            "relationship_ids": [str(record["relationship_id"]) for record in missing],
        },
    }

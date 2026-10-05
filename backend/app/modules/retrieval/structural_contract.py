"""Compatibility exports for structural build validation."""

from app.platform.domain.structural_contract import (
    STRUCTURAL_CONTRACT_VERSION,
    validate_structural_manifest,
    verify_semantic_scope_snapshot,
    verify_structural_build,
)

__all__ = [
    "STRUCTURAL_CONTRACT_VERSION",
    "validate_structural_manifest",
    "verify_semantic_scope_snapshot",
    "verify_structural_build",
]

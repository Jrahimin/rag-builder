"""Regression coverage for persisted index-build enum values."""

from app.models.index_build import IndexBuild, IndexBuildOperation, IndexBuildState


def test_index_build_enums_read_database_values() -> None:
    state_type = IndexBuild.__table__.c.state.type
    operation_type = IndexBuild.__table__.c.operation.type

    assert state_type.enums == [member.value for member in IndexBuildState]
    assert operation_type.enums == [member.value for member in IndexBuildOperation]

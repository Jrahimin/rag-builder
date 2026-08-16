"""Job configuration snapshot identity regressions."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import Settings
from app.modules.jobs.repositories.job_configuration_repository import (
    JobConfigurationRepository,
)
from app.platform.jobs.configuration import build_job_configuration

pytestmark = pytest.mark.unit


async def test_snapshot_dedup_uses_output_digest_not_execution_provenance() -> None:
    session = AsyncMock()
    inserted_id = uuid.uuid4()
    snapshot = MagicMock(id=inserted_id)
    inserted = MagicMock()
    inserted.scalar_one_or_none.return_value = inserted_id
    fetched = MagicMock()
    fetched.scalar_one_or_none.return_value = snapshot
    session.execute.side_effect = [inserted, fetched]
    configuration = build_job_configuration(
        Settings(),
        active_index_build_id=str(uuid.uuid4()),
        source_metadata_generation=17,
    )
    expected = configuration.output_digest()
    with (
        patch.object(type(configuration), "output_digest", return_value=expected) as output_digest,
        patch.object(
            type(configuration),
            "digest",
            side_effect=AssertionError("full digest used"),
        ) as digest,
    ):
        result = await JobConfigurationRepository(session, uuid.uuid4()).get_or_create(
            configuration
        )

    assert result is snapshot
    output_digest.assert_called_once_with()
    digest.assert_not_called()

"""Ordinary evaluation APIs cannot publish private proof; captures are bounded/expiring."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.composition.message_diagnostic_retention import expire_message_diagnostics
from app.core.exceptions import UnauthorizedError
from app.dependencies.admin_auth import current_admin
from app.models.audit_event import AuditEvent
from app.models.evaluation_diagnostic import EvaluationDiagnostic
from app.models.evaluation_run import EvaluationRun
from app.modules.evaluation.repositories.evaluation_diagnostic_repository import (
    EvaluationDiagnosticRepository,
)
from tests.integration.knowledge_helpers import run_captured_evaluation_jobs

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_evaluation_public_legacy_sanitation_and_controlled_capture_expiry(
    db_client, integration_connection, captured_jobs
):
    project = (
        await db_client.post("/api/v1/projects", json={"name": "privacy " + uuid.uuid4().hex})
    ).json()["data"]["id"]
    path = f"/api/v1/projects/{project}/evaluations"
    dataset = await db_client.post(
        path + "/datasets",
        json={
            "name": "privacy",
            "version": "1",
            "cases": [
                {
                    "key": "missing",
                    "kind": "no_answer",
                    "query": "Unknown payroll rule?",
                    "expected_no_answer": True,
                }
            ],
        },
    )
    assert dataset.status_code == 201, dataset.text
    body = {"dataset_id": dataset.json()["data"]["id"]}
    ordinary = await db_client.post(path + "/runs", json=body)
    assert ordinary.status_code == 202, ordinary.text
    run_id = uuid.UUID(ordinary.json()["data"]["id"])
    await run_captured_evaluation_jobs(integration_connection, captured_jobs)
    async with AsyncSession(
        bind=integration_connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    ) as session:
        assert not (await EvaluationDiagnosticRepository(session, uuid.UUID(project)).get(run_id))
        run = await session.get(EvaluationRun, run_id)
        run.case_results = [
            {
                "operator_diagnostic": {"rejected_attempts": [{"text": "PRIVATE-REJECTED"}]},
                "execution": {"nested": {"operator_diagnostic": "PRIVATE-PROOF"}},
            }
        ]
        await session.commit()
    for endpoint in (path + f"/runs/{run_id}", path + "/runs", path + "/quality"):
        response = await db_client.get(endpoint)
        assert response.status_code == 200, response.text
        assert "PRIVATE" not in response.text and "operator_diagnostic" not in response.text
    csrf = {"X-CSRF-Token": "fixture", "Cookie": "ape_admin_csrf=fixture"}
    captured = await db_client.post(path + "/runs/captured", json=body, headers=csrf)
    assert captured.status_code == 202, captured.text
    capture_id = uuid.UUID(captured.json()["data"]["id"])
    await run_captured_evaluation_jobs(integration_connection, captured_jobs)
    assert "operator_diagnostic" not in (await db_client.get(path + f"/runs/{capture_id}")).text
    private = await db_client.get(path + f"/runs/{capture_id}/diagnostics")
    assert private.status_code == 200 and private.json()["data"], private.text
    assert any(row["payload"] is not None for row in private.json()["data"])

    async def denied():
        raise UnauthorizedError("Ordinary credentials do not grant operator access")

    app = db_client._transport.app
    app.dependency_overrides[current_admin] = denied
    try:
        denied_read = await db_client.get(path + f"/runs/{capture_id}/diagnostics")
        assert denied_read.status_code == 401
        denied_capture = await db_client.post(path + "/runs/captured", json=body, headers=csrf)
        assert denied_capture.status_code == 401
    finally:
        app.dependency_overrides.pop(current_admin, None)
    async with AsyncSession(
        bind=integration_connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    ) as session:
        repository = EvaluationDiagnosticRepository(session, uuid.UUID(project))
        await repository.capture(
            capture_id,
            "oversize",
            "fixture",
            {"rejected_attempts": [{"text": "X" * 300000}], "api_key": "NEVER"},
        )
        await session.flush()
        oversized = await session.scalar(
            select(EvaluationDiagnostic).where(
                EvaluationDiagnostic.run_id == capture_id,
                EvaluationDiagnostic.case_key == "oversize",
            )
        )
        assert oversized.payload["truncated"] is True and "api_key" not in oversized.payload
        rows = await repository.get(capture_id)
        for row in rows:
            row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        # Leave one unexpired payload beside the expired rows; the background sweep
        # must clear inactive-project payloads without another diagnostic read/capture.
        live = EvaluationDiagnostic(
            project_id=uuid.UUID(project),
            run_id=capture_id,
            case_key="unexpired",
            profile="fixture",
            payload={"terminal": {"outcome": "retained"}},
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
        session.add(live)
        await session.commit()

    def sweep_session():
        return AsyncSession(
            bind=integration_connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )

    await expire_message_diagnostics(sweep_session)
    async with sweep_session() as session:
        stored = list(
            await session.scalars(
                select(EvaluationDiagnostic).where(EvaluationDiagnostic.run_id == capture_id)
            )
        )
        assert all(row.payload is None for row in stored if row.case_key != "unexpired")
        assert next(row for row in stored if row.case_key == "unexpired").payload is not None
    expired = await db_client.get(path + f"/runs/{capture_id}/diagnostics")
    assert expired.status_code == 200 and all(
        row["payload"] is None for row in expired.json()["data"] if row["case_key"] != "unexpired"
    )
    async with AsyncSession(bind=integration_connection) as session:
        events = (
            (
                await session.execute(
                    select(AuditEvent).where(AuditEvent.project_id == uuid.UUID(project))
                )
            )
            .scalars()
            .all()
        )
        names = {row.event_type for row in events}
        assert "evaluation.diagnostic.capture" in names and "evaluation.diagnostic.read" in names

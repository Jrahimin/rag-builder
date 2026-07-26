"""Integration tests for the contextual generation API."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from app.platform.providers.contracts.llm import ChatCompletionResult, ChatMessage
from app.platform.providers.errors import ProviderError
from app.platform.providers.implementations.echo_chat import EchoLLMProvider

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def _create_project(client: AsyncClient, prefix: str = "Generation") -> str:
    response = await client.post(
        "/api/v1/projects",
        json={"name": f"{prefix} {uuid.uuid4().hex[:8]}"},
    )
    assert response.status_code == 201
    return response.json()["data"]["id"]


def _payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "use_case": "contextual_answer",
        "input": {"question": "What is the refund period?"},
        "context": {
            "policy": {"refund_days": 30},
            "notes": ["Applies to unused products", "Receipt required"],
        },
    }
    payload.update(overrides)
    return payload


async def test_create_and_get_contextual_generation(db_client: AsyncClient) -> None:
    project_id = await _create_project(db_client)

    response = await db_client.post(
        f"/api/v1/projects/{project_id}/generations",
        json=_payload(),
        headers={"Idempotency-Key": f"refund-{uuid.uuid4()}"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    generation = body["data"]
    assert generation["status"] == "succeeded"
    assert isinstance(generation["output"], str)
    assert generation["grounded"] is True
    assert generation["provider"] == "echo"
    assert generation["prompt_version"] == "v1"
    assert generation["schema_version"] == "v1"
    assert generation["request_id"]
    assert generation["trace_id"]
    assert body["meta"]["request_id"]

    detail = await db_client.get(f"/api/v1/projects/{project_id}/generations/{generation['id']}")
    assert detail.status_code == 200
    assert detail.json()["data"]["id"] == generation["id"]
    assert detail.json()["data"]["output"] == generation["output"]


async def test_invalid_context_and_unknown_use_case(db_client: AsyncClient) -> None:
    project_id = await _create_project(db_client)

    invalid = await db_client.post(
        f"/api/v1/projects/{project_id}/generations",
        json=_payload(context=[]),
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "validation_error"

    unknown = await db_client.post(
        f"/api/v1/projects/{project_id}/generations",
        json=_payload(use_case="not_registered"),
    )
    assert unknown.status_code == 400
    assert unknown.json()["error"]["code"] == "unknown_generation_use_case"


async def test_schema_validation_failure_persists_failed_trace(
    db_client: AsyncClient,
    integration_connection,
) -> None:
    project_id = await _create_project(db_client)

    response = await db_client.post(
        f"/api/v1/projects/{project_id}/generations",
        json=_payload(
            response_schema={
                "type": "object",
                "properties": {"answer": {"type": "string"}},
                "required": ["answer"],
            }
        ),
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "generation_output_schema_mismatch"
    row = (
        await integration_connection.execute(
            text(
                "SELECT status, error_code, input_tokens, output_tokens "
                "FROM generations WHERE project_id = :project_id "
                "ORDER BY created_at DESC LIMIT 1"
            ),
            {"project_id": uuid.UUID(project_id)},
        )
    ).one()
    assert row.status == "failed"
    assert row.error_code == "generation_output_schema_mismatch"
    assert row.input_tokens is not None
    assert row.output_tokens is not None


async def test_provider_failure_persists_failed_trace(
    db_client: AsyncClient,
    integration_connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.dependencies import generation as generation_dependencies

    class FailingProvider(EchoLLMProvider):
        async def generate(
            self,
            messages: list[ChatMessage],
            *,
            temperature: float,
            max_tokens: int,
        ) -> ChatCompletionResult:
            del messages, temperature, max_tokens
            raise ProviderError("offline", provider_name="echo")

    monkeypatch.setattr(
        generation_dependencies,
        "create_llm_provider_for_config",
        lambda settings, *, provider, model: FailingProvider(
            model=model or settings.llm.model,
            provider_version="test",
        ),
    )
    project_id = await _create_project(db_client)

    response = await db_client.post(
        f"/api/v1/projects/{project_id}/generations",
        json=_payload(),
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "llm_provider_unavailable"
    row = (
        await integration_connection.execute(
            text(
                "SELECT status, error_code FROM generations "
                "WHERE project_id = :project_id ORDER BY created_at DESC LIMIT 1"
            ),
            {"project_id": uuid.UUID(project_id)},
        )
    ).one()
    assert row.status == "failed"
    assert row.error_code == "llm_provider_unavailable"


async def test_project_isolation_hides_generation(db_client: AsyncClient) -> None:
    owner_project_id = await _create_project(db_client, "Owner")
    other_project_id = await _create_project(db_client, "Other")
    created = await db_client.post(
        f"/api/v1/projects/{owner_project_id}/generations",
        json=_payload(),
    )
    generation_id = created.json()["data"]["id"]

    response = await db_client.get(
        f"/api/v1/projects/{other_project_id}/generations/{generation_id}"
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "generation_not_found"


async def test_idempotency_replays_and_rejects_payload_change(
    db_client: AsyncClient,
) -> None:
    project_id = await _create_project(db_client)
    key = f"invoice-{uuid.uuid4()}"
    headers = {"Idempotency-Key": key}

    first = await db_client.post(
        f"/api/v1/projects/{project_id}/generations",
        json=_payload(),
        headers=headers,
    )
    replay = await db_client.post(
        f"/api/v1/projects/{project_id}/generations",
        json=_payload(),
        headers=headers,
    )
    conflict = await db_client.post(
        f"/api/v1/projects/{project_id}/generations",
        json=_payload(input={"question": "Changed"}),
        headers=headers,
    )

    assert first.status_code == 201
    assert replay.status_code == 201
    assert replay.json()["data"]["id"] == first.json()["data"]["id"]
    assert replay.json()["data"]["idempotency_replayed"] is True
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "generation_idempotency_conflict"


@pytest.mark.parametrize(
    ("retention", "payload_retained"),
    [("none", False), ("metadata_only", False), ("full", True)],
)
async def test_retention_behavior(
    db_client: AsyncClient,
    integration_connection,
    retention: str,
    payload_retained: bool,
) -> None:
    project_id = await _create_project(db_client)
    response = await db_client.post(
        f"/api/v1/projects/{project_id}/generations",
        json=_payload(retention=retention),
    )

    assert response.status_code == 201
    generation_id = response.json()["data"]["id"]
    assert response.json()["data"]["payload_retained"] is payload_retained
    row = (
        await integration_connection.execute(
            text(
                "SELECT retained_input, retained_context, payload_metadata "
                "FROM generations WHERE id = :generation_id"
            ),
            {"generation_id": uuid.UUID(generation_id)},
        )
    ).one()
    if retention == "full":
        assert row.retained_input is not None
        assert row.retained_context is not None
    else:
        assert row.retained_input is None
        assert row.retained_context is None
    if retention == "metadata_only":
        assert "context_shape" in row.payload_metadata

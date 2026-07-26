"""Payload size, shape, retention, and hashing for contextual generation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from app.core.config import GenerationConfig, GenerationRetentionMode
from app.core.exceptions import BadRequestError, PayloadTooLargeError, ValidationError
from app.modules.generation.schemas.generation import GenerationCreateRequest


def canonical_json(value: Any) -> str:
    """Serialize JSON data deterministically for hashes, budgets, and prompts."""
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def sha256_text(value: str) -> str:
    """Return a lowercase SHA-256 digest."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_json(value: Any) -> str:
    """Return a SHA-256 digest for canonical JSON data."""
    return sha256_text(canonical_json(value))


@dataclass(frozen=True, slots=True)
class ValidatedGenerationPayload:
    """Validated canonical payload and safe persistence metadata."""

    canonical_input: str
    canonical_context: str
    retention: GenerationRetentionMode
    retained_input: Any | None
    retained_context: Any | None
    metadata: dict[str, Any]


class PayloadValidationService:
    """Reject oversized or structurally unsafe context before provider calls."""

    def __init__(self, config: GenerationConfig) -> None:
        self._config = config

    def validate(self, request: GenerationCreateRequest) -> ValidatedGenerationPayload:
        request_json = canonical_json(request.model_dump(mode="json", exclude_none=True))
        request_bytes = len(request_json.encode("utf-8"))
        if request_bytes > self._config.max_request_bytes:
            raise PayloadTooLargeError(
                message="The contextual generation request exceeds the configured size limit.",
                code="generation_payload_too_large",
            )

        canonical_input = canonical_json(request.input)
        canonical_context = canonical_json(request.context)
        context_bytes = len(canonical_context.encode("utf-8"))
        if context_bytes > self._config.max_context_bytes:
            raise PayloadTooLargeError(
                message="The contextual generation context exceeds the configured size limit.",
                code="generation_context_too_large",
            )

        node_count = self._validate_tree(request.context)
        retention = request.retention or self._config.default_retention
        if retention is GenerationRetentionMode.FULL and not self._config.allow_full_retention:
            raise BadRequestError(
                message="Full contextual generation payload retention is disabled.",
                code="generation_full_retention_disabled",
            )

        metadata: dict[str, Any] = {
            "request_bytes": request_bytes,
            "input_bytes": len(canonical_input.encode("utf-8")),
            "context_bytes": context_bytes,
            "input_sha256": sha256_text(canonical_input),
            "context_sha256": sha256_text(canonical_context),
            "context_nodes": node_count,
        }
        if retention is not GenerationRetentionMode.NONE:
            metadata["input_shape"] = self._shape(request.input)
            metadata["context_shape"] = self._shape(request.context)

        return ValidatedGenerationPayload(
            canonical_input=canonical_input,
            canonical_context=canonical_context,
            retention=retention,
            retained_input=(request.input if retention is GenerationRetentionMode.FULL else None),
            retained_context=(
                request.context if retention is GenerationRetentionMode.FULL else None
            ),
            metadata=metadata,
        )

    def validate_schema_size(self, schema: dict[str, Any]) -> None:
        try:
            schema_bytes = len(canonical_json(schema).encode("utf-8"))
        except (TypeError, ValueError) as exc:
            raise ValidationError(
                message="The response schema must be valid JSON.",
                code="generation_response_schema_invalid",
            ) from exc
        if schema_bytes > self._config.max_schema_bytes:
            raise PayloadTooLargeError(
                message="The contextual generation response schema is too large.",
                code="generation_schema_too_large",
            )

    def _validate_tree(self, value: Any) -> int:
        node_count = 0
        stack: list[tuple[Any, int]] = [(value, 1)]
        while stack:
            current, depth = stack.pop()
            node_count += 1
            if node_count > self._config.max_context_nodes:
                raise ValidationError(
                    message="The context contains too many nested values.",
                    code="generation_context_invalid",
                )
            if depth > self._config.max_context_depth:
                raise ValidationError(
                    message="The context exceeds the configured nesting depth.",
                    code="generation_context_invalid",
                )
            if isinstance(current, dict):
                for key, child in current.items():
                    if not key.strip():
                        raise ValidationError(
                            message="Context object keys must not be empty.",
                            code="generation_context_invalid",
                        )
                    stack.append((child, depth + 1))
            elif isinstance(current, list):
                stack.extend((child, depth + 1) for child in current)
        return node_count

    def _shape(self, value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return {
                "type": "object",
                "item_count": len(value),
                "keys": sorted(value)[:50],
            }
        if isinstance(value, list):
            return {"type": "array", "item_count": len(value)}
        if isinstance(value, str):
            return {"type": "string", "characters": len(value)}
        if value is None:
            return {"type": "null"}
        if isinstance(value, bool):
            return {"type": "boolean"}
        return {"type": "number"}

"""JSON Schema validation for contextual generation output."""

from __future__ import annotations

import json
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError

from app.core.exceptions import ValidationError
from app.modules.generation.errors import GenerationOutputValidationError


class OutputValidationService:
    """Validate caller schemas and provider output with JSON Schema 2020-12."""

    def validate_schema(self, schema: dict[str, Any]) -> None:
        self._reject_external_references(schema)
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as exc:
            raise ValidationError(
                message="The response schema is not a valid JSON Schema.",
                code="generation_response_schema_invalid",
            ) from exc

    def parse_and_validate(self, content: str, schema: dict[str, Any]) -> Any:
        output = self._parse(content, schema)
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        error = next(validator.iter_errors(output), None)
        if error is not None:
            path = ".".join(str(part) for part in error.absolute_path)
            raise GenerationOutputValidationError(
                context={
                    "schema_path": path or "$",
                    "validation_error": error.message,
                }
            )
        return output

    def _parse(self, content: str, schema: dict[str, Any]) -> Any:
        schema_type = schema.get("type")
        accepts_string = schema_type == "string" or (
            isinstance(schema_type, list) and "string" in schema_type
        )
        if accepts_string:
            return content
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return content

    def _reject_external_references(self, schema: Any) -> None:
        stack = [schema]
        while stack:
            current = stack.pop()
            if isinstance(current, dict):
                reference = current.get("$ref")
                if isinstance(reference, str) and not reference.startswith("#"):
                    raise ValidationError(
                        message="External JSON Schema references are not supported.",
                        code="generation_response_schema_invalid",
                    )
                stack.extend(current.values())
            elif isinstance(current, list):
                stack.extend(current)

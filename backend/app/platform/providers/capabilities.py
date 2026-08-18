"""Versioned provider/model capabilities and neutral parameter translation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.config import EmbeddingBackend, EmbeddingConfig, LLMBackend, LLMConfig
from app.core.exceptions import BadRequestError

CAPABILITY_VERSION = "2026-08-16.v2"


def llm_credential_configured(config: LLMConfig) -> bool | None:
    """Report credential readiness without leaking provider branches to consumers."""
    if config.backend in {LLMBackend.OPENAI, LLMBackend.OPENAI_COMPATIBLE}:
        return bool(config.openai_api_key)
    if config.backend is LLMBackend.GEMINI:
        return bool(config.gemini_api_key)
    return None


def embedding_credential_configured(
    config: EmbeddingConfig,
    *,
    cohere_api_key: str = "",
) -> bool | None:
    """Report embedding credential readiness through the provider abstraction."""
    if config.backend is EmbeddingBackend.OPENAI:
        return bool(config.openai_api_key)
    if config.backend is EmbeddingBackend.GEMINI:
        return bool(config.gemini_api_key)
    if config.backend is EmbeddingBackend.COHERE:
        return bool(cohere_api_key.strip())
    return None


@dataclass(frozen=True, slots=True)
class ParameterCapability:
    supported: bool
    wire_name: str | None
    minimum: float | int | None = None
    maximum: float | int | None = None
    omit_when_none: bool = True


@dataclass(frozen=True, slots=True)
class ProviderModelCapability:
    provider: str
    model: str
    capability_version: str
    temperature: ParameterCapability
    max_tokens: ParameterCapability
    supports_stream_usage: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "capability_version": self.capability_version,
            "supports_stream_usage": self.supports_stream_usage,
            "parameters": {
                "temperature": _parameter_dict(self.temperature),
                "max_tokens": _parameter_dict(self.max_tokens),
            },
        }


def describe_llm_capability(provider: str, model: str) -> ProviderModelCapability:
    """Return the deterministic capability descriptor for a provider/model pair."""
    try:
        backend = LLMBackend(provider)
    except ValueError:
        raise BadRequestError(
            message=f"Unsupported LLM provider: {provider}",
            code="unsupported_llm_provider",
        ) from None
    normalized_model = model.strip()
    if not normalized_model:
        raise BadRequestError(
            message="LLM model must not be blank.",
            code="unsupported_llm_model",
        )

    normalized = normalized_model.lower()
    temperature_supported = not (
        backend is LLMBackend.OPENAI
        and normalized.startswith(("o1", "o3", "o4", "gpt-5"))
    )
    token_wire_names = {
        LLMBackend.OPENAI: "max_completion_tokens",
        LLMBackend.OPENAI_COMPATIBLE: "max_completion_tokens",
        LLMBackend.GEMINI: "maxOutputTokens",
        LLMBackend.OLLAMA: "num_predict",
        LLMBackend.ECHO: None,
    }
    return ProviderModelCapability(
        provider=backend.value,
        model=normalized_model,
        capability_version=CAPABILITY_VERSION,
        temperature=ParameterCapability(
            supported=temperature_supported,
            wire_name="temperature" if temperature_supported else None,
            minimum=0.0,
            maximum=2.0,
        ),
        max_tokens=ParameterCapability(
            supported=True,
            wire_name=token_wire_names[backend],
            minimum=1,
            # Ollama model contexts are deployment artifacts, not a provider-
            # wide constant. The adapter validates num_predict against /api/show
            # before sending generation requests.
            maximum=None if backend is LLMBackend.OLLAMA else 128_000,
            omit_when_none=False,
        ),
        supports_stream_usage=backend is LLMBackend.OPENAI,
    )


def validate_generation_parameters(
    capability: ProviderModelCapability,
    *,
    temperature: float | None,
    max_tokens: int,
) -> None:
    """Reject unsupported or out-of-range neutral generation parameters."""
    if temperature is not None:
        if not capability.temperature.supported:
            raise BadRequestError(
                message=(
                    f"temperature is not supported by {capability.provider}/{capability.model}."
                ),
                code="unsupported_provider_parameter",
                context={"parameter": "temperature"},
            )
        _validate_range("temperature", temperature, capability.temperature)
    _validate_range("max_tokens", max_tokens, capability.max_tokens)


def translate_generation_parameters(
    capability: ProviderModelCapability,
    *,
    temperature: float | None,
    max_tokens: int,
) -> dict[str, float | int]:
    """Validate neutral parameters and map them to provider wire names."""
    validate_generation_parameters(
        capability,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    translated: dict[str, float | int] = {}
    if temperature is not None and capability.temperature.wire_name is not None:
        translated[capability.temperature.wire_name] = temperature
    if capability.max_tokens.wire_name is not None:
        translated[capability.max_tokens.wire_name] = max_tokens
    return translated


def _validate_range(
    name: str,
    value: float | int,
    capability: ParameterCapability,
) -> None:
    if capability.minimum is not None and value < capability.minimum:
        raise BadRequestError(
            message=f"{name} is below the provider/model minimum.",
            code="provider_parameter_out_of_range",
        )
    if capability.maximum is not None and value > capability.maximum:
        raise BadRequestError(
            message=f"{name} exceeds the provider/model maximum.",
            code="provider_parameter_out_of_range",
        )


def _parameter_dict(value: ParameterCapability) -> dict[str, Any]:
    return {
        "supported": value.supported,
        "wire_name": value.wire_name,
        "minimum": value.minimum,
        "maximum": value.maximum,
        "omit_when_none": value.omit_when_none,
    }

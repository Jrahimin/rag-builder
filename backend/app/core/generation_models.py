"""Code-owned logical generation-model registry and deployment allowlist resolution."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import LLMBackend, Settings
from app.core.exceptions import BadRequestError

GENERATION_MODEL_REGISTRY_VERSION = "2026-09-02.v1"
DEPLOYMENT_DEFAULT_MODEL_ID = "deployment-default"


@dataclass(frozen=True, slots=True)
class GenerationModelDefinition:
    id: str
    provider: LLMBackend | None
    model: str | None
    implementation: str
    supports_temperature: bool = True


GENERATION_MODEL_REGISTRY: dict[str, GenerationModelDefinition] = {
    DEPLOYMENT_DEFAULT_MODEL_ID: GenerationModelDefinition(
        id=DEPLOYMENT_DEFAULT_MODEL_ID,
        provider=None,
        model=None,
        implementation="deployment_owned",
    ),
    "openai-gpt-5.6-luna": GenerationModelDefinition(
        id="openai-gpt-5.6-luna",
        provider=LLMBackend.OPENAI,
        model="gpt-5.6-luna",
        implementation="openai_responses",
        supports_temperature=False,
    ),
    "openai-gpt-4o-mini": GenerationModelDefinition(
        id="openai-gpt-4o-mini",
        provider=LLMBackend.OPENAI,
        model="gpt-4o-mini",
        implementation="openai_responses",
    ),
}


@dataclass(frozen=True, slots=True)
class ResolvedGenerationModel:
    id: str
    provider: LLMBackend
    model: str
    implementation: str


def validate_generation_model_allowlist(settings: Settings) -> list[str]:
    allowed, default = generation_model_policy(settings)
    errors: list[str] = []
    unknown = sorted(set(allowed) - GENERATION_MODEL_REGISTRY.keys())
    if unknown:
        errors.append("unknown generation model IDs: " + ", ".join(unknown))
    if default not in allowed:
        errors.append("default generation model ID must be in the deployment allowlist")
    try:
        resolve_generation_model(settings, default)
    except BadRequestError as exc:
        errors.append(exc.message)
    return errors


def resolve_generation_model(
    settings: Settings,
    model_id: str | None,
) -> ResolvedGenerationModel:
    allowed, default = generation_model_policy(settings)
    selected = model_id or default
    if selected not in allowed:
        raise BadRequestError(
            message="The generation model is not enabled for this deployment.",
            code="generation_model_not_allowed",
            context={"generation_model_id": selected},
        )
    definition = GENERATION_MODEL_REGISTRY.get(selected)
    if definition is None:
        raise BadRequestError(
            message="The generation model ID is not registered.",
            code="generation_model_not_registered",
            context={"generation_model_id": selected},
        )
    return ResolvedGenerationModel(
        id=selected,
        provider=definition.provider or settings.llm.backend,
        model=definition.model or settings.llm.model,
        implementation=definition.implementation,
    )


def generation_model_id_for_legacy_pair(
    settings: Settings,
    *,
    provider: LLMBackend,
    model: str,
) -> str | None:
    allowed, _ = generation_model_policy(settings)
    for model_id in allowed:
        try:
            resolved = resolve_generation_model(settings, model_id)
        except BadRequestError:
            continue
        if resolved.provider is provider and resolved.model == model:
            return model_id
    return None


def generation_model_policy(settings: Settings) -> tuple[tuple[str, ...], str]:
    """Resolve the exact logical-model allowlist without inventing commercial tiers."""
    if settings.runtime.capability_profile_id is not None:
        # Local import keeps the registry dependency one-way while avoiding a
        # second hard-coded capability policy that can drift from profiles.py.
        from app.core.capability_profiles import deployment_capability_profile

        capability = deployment_capability_profile(settings)
        return (
            capability.allowed_generation_model_ids,
            capability.default_generation_model_id,
        )
    return (
        tuple(settings.ai_policy.allowed_generation_model_ids),
        settings.ai_policy.default_generation_model_id,
    )

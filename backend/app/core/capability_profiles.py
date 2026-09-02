"""Code-owned deployment capability identities shared by core validation and profiles."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from app.core.config import (
    EmbeddingBackend,
    LLMBackend,
    RerankerBackend,
    RuntimeProfile,
    Settings,
    WebSearchBackend,
)
from app.core.exceptions import BadRequestError


@dataclass(frozen=True, slots=True)
class DeploymentCapabilityProfile:
    id: str
    legacy_runtime_alias: RuntimeProfile
    llm_provider: LLMBackend | None
    embedding_provider: EmbeddingBackend | None
    reranker_provider: RerankerBackend | None
    web_search_provider: WebSearchBackend | None
    allowed_generation_model_ids: tuple[str, ...]
    default_generation_model_id: str
    calibration_profile_id: str
    # Remains unset until one seeded Test Lab candidate clears certification.
    default_index_profile_id: str
    feature_flags: tuple[str, ...]


DEPLOYMENT_CAPABILITY_PROFILES: Mapping[str, DeploymentCapabilityProfile] = MappingProxyType(
    {
        profile.id: profile
        for profile in (
            DeploymentCapabilityProfile(
                id="development",
                legacy_runtime_alias=RuntimeProfile.DEVELOPMENT,
                llm_provider=None,
                embedding_provider=None,
                reranker_provider=None,
                web_search_provider=None,
                allowed_generation_model_ids=("deployment-default",),
                default_generation_model_id="deployment-default",
                calibration_profile_id="hash-local-whole-chunk",
                default_index_profile_id="development-hash",
                feature_flags=("test_lab", "ephemeral_tuning"),
            ),
            DeploymentCapabilityProfile(
                id="hosted-managed",
                legacy_runtime_alias=RuntimeProfile.HOSTED_MANAGED,
                llm_provider=LLMBackend.OPENAI,
                embedding_provider=EmbeddingBackend.COHERE,
                reranker_provider=RerankerBackend.COHERE,
                web_search_provider=WebSearchBackend.OPENAI,
                allowed_generation_model_ids=("openai-gpt-5.6-luna",),
                default_generation_model_id="openai-gpt-5.6-luna",
                calibration_profile_id="cohere-v4-managed-whole-chunk",
                default_index_profile_id="hosted-cohere-v4",
                feature_flags=("hosted", "web_search", "query_translation", "rerank_fail_open"),
            ),
            DeploymentCapabilityProfile(
                id="hosted-openai",
                legacy_runtime_alias=RuntimeProfile.HOSTED_OPENAI,
                llm_provider=LLMBackend.OPENAI,
                embedding_provider=EmbeddingBackend.OPENAI,
                reranker_provider=RerankerBackend.COHERE,
                web_search_provider=WebSearchBackend.OPENAI,
                allowed_generation_model_ids=("deployment-default", "openai-gpt-4o-mini"),
                default_generation_model_id="deployment-default",
                calibration_profile_id="openai-large-cohere-whole-chunk",
                default_index_profile_id="hosted-openai-large",
                feature_flags=("hosted", "web_search", "query_translation", "rerank_fail_open"),
            ),
            DeploymentCapabilityProfile(
                id="private-ollama",
                legacy_runtime_alias=RuntimeProfile.PRIVATE_OLLAMA,
                llm_provider=LLMBackend.OLLAMA,
                embedding_provider=EmbeddingBackend.OLLAMA,
                reranker_provider=RerankerBackend.LEXICAL,
                web_search_provider=WebSearchBackend.DISABLED,
                allowed_generation_model_ids=("deployment-default",),
                default_generation_model_id="deployment-default",
                calibration_profile_id="ollama-1024-local-whole-chunk",
                default_index_profile_id="private-ollama-1024",
                feature_flags=("private", "rerank_fail_open"),
            ),
        )
    }
)

_LEGACY_DEPLOYMENT_ALIASES: Mapping[RuntimeProfile, str] = MappingProxyType(
    {
        profile.legacy_runtime_alias: profile.id
        for profile in DEPLOYMENT_CAPABILITY_PROFILES.values()
    }
)


def deployment_capability_profile(settings: Settings) -> DeploymentCapabilityProfile:
    """Resolve the canonical capability identity, retaining legacy runtime aliases."""
    profile_id = settings.runtime.capability_profile_id
    if profile_id is None:
        profile_id = _LEGACY_DEPLOYMENT_ALIASES[settings.runtime.profile]
    try:
        return DEPLOYMENT_CAPABILITY_PROFILES[profile_id]
    except KeyError as exc:
        raise BadRequestError(
            message="The deployment capability profile is not registered.",
            code="deployment_profile_not_registered",
            context={"profile_id": profile_id},
        ) from exc

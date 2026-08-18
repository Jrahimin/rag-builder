"""Secret-free durable job configuration capture and restoration."""

from __future__ import annotations

from app.core.config import Settings
from app.platform.config.project_ai import (
    EffectiveConfigResolution,
    apply_effective_ai_config,
    resolve_project_ai_config,
)
from app.platform.jobs.contracts import JobConfiguration

_EMBEDDING_SECRET_FIELDS = {"openai_api_key", "gemini_api_key"}
_LLM_SECRET_FIELDS = {"openai_api_key", "gemini_api_key"}
_OCR_SECRET_FIELDS = {"google_api_key"}


def build_job_configuration(
    settings: Settings,
    *,
    resolution: EffectiveConfigResolution | None = None,
    active_index_build_id: str | None = None,
    source_metadata_generation: int = 0,
) -> JobConfiguration:
    """Capture every setting that can change process/embed/index outputs."""
    effective_resolution = resolution or resolve_project_ai_config(settings, None)
    effective_settings = apply_effective_ai_config(settings, effective_resolution)
    return JobConfiguration(
        processing={
            "parsing": effective_settings.parsing.model_dump(mode="json"),
            "chunking": effective_settings.chunking.model_dump(mode="json"),
            "ocr": effective_settings.ocr.model_dump(mode="json", exclude=_OCR_SECRET_FIELDS),
        },
        index={
            # Project AI policy does not own embedding or index construction.
            # Keep this section on staged platform settings so chat, LLM, and
            # retrieval-policy edits cannot change corpus identity.
            "embedding": settings.embedding.model_dump(
                mode="json",
                exclude=_EMBEDDING_SECRET_FIELDS,
            ),
            "retrieval": settings.retrieval.model_dump(mode="json"),
        },
        quality={
            "chat": effective_settings.chat.model_dump(mode="json"),
            "evaluation": effective_settings.evaluation.model_dump(mode="json"),
            "llm": effective_settings.llm.model_dump(mode="json", exclude=_LLM_SECRET_FIELDS),
            "query_translation": effective_settings.query_translation.model_dump(mode="json"),
            "reranker": effective_settings.reranker.model_dump(
                mode="json",
                exclude={"cohere_api_key"},
            ),
            "cohere": {"configured": bool(settings.resolved_cohere_api_key())},
        },
        execution=effective_resolution.secret_free_snapshot(),
        provenance={
            **effective_resolution.provenance.model_dump(mode="json"),
            "active_index_build_id": active_index_build_id,
            "source_metadata_generation": source_metadata_generation,
        },
    )


def apply_job_configuration(
    settings: Settings,
    configuration: JobConfiguration,
) -> Settings:
    """Overlay a stored snapshot while retaining live infrastructure secrets."""
    processing = configuration.processing
    index = configuration.index
    quality = configuration.quality
    return settings.model_copy(
        update={
            "parsing": type(settings.parsing).model_validate(processing["parsing"]),
            "chunking": type(settings.chunking).model_validate(processing["chunking"]),
            "ocr": type(settings.ocr).model_validate(
                {
                    **settings.ocr.model_dump(),
                    **processing["ocr"],
                }
            ),
            "embedding": type(settings.embedding).model_validate(
                {
                    **settings.embedding.model_dump(),
                    **index["embedding"],
                }
            ),
            "retrieval": type(settings.retrieval).model_validate(index["retrieval"]),
            "chat": type(settings.chat).model_validate(quality["chat"]),
            "evaluation": type(settings.evaluation).model_validate(quality["evaluation"]),
            "llm": type(settings.llm).model_validate(
                {
                    **settings.llm.model_dump(),
                    **quality["llm"],
                }
            ),
            "query_translation": type(settings.query_translation).model_validate(
                quality.get("query_translation", settings.query_translation.model_dump())
            ),
            "reranker": type(settings.reranker).model_validate(
                {
                    **settings.reranker.model_dump(),
                    **quality.get("reranker", {}),
                }
            ),
        }
    )

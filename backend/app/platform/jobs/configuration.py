"""Secret-free durable job configuration capture and restoration."""

from __future__ import annotations

from app.core.config import Settings
from app.platform.jobs.contracts import JobConfiguration

_EMBEDDING_SECRET_FIELDS = {"openai_api_key", "gemini_api_key"}
_LLM_SECRET_FIELDS = {"openai_api_key", "gemini_api_key"}


def build_job_configuration(settings: Settings) -> JobConfiguration:
    """Capture every setting that can change process/embed/index outputs."""
    return JobConfiguration(
        processing={
            "parsing": settings.parsing.model_dump(mode="json"),
            "chunking": settings.chunking.model_dump(mode="json"),
            "ocr": settings.ocr.model_dump(mode="json"),
        },
        index={
            "embedding": settings.embedding.model_dump(
                mode="json",
                exclude=_EMBEDDING_SECRET_FIELDS,
            ),
            "retrieval": settings.retrieval.model_dump(mode="json"),
        },
        quality={
            "chat": settings.chat.model_dump(mode="json"),
            "evaluation": settings.evaluation.model_dump(mode="json"),
            "llm": settings.llm.model_dump(mode="json", exclude=_LLM_SECRET_FIELDS),
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
            "ocr": type(settings.ocr).model_validate(processing["ocr"]),
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
        }
    )

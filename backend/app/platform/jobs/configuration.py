"""Secret-free durable job configuration capture and restoration."""

from __future__ import annotations

from app.core.config import Settings
from app.platform.jobs.contracts import JobConfiguration

_EMBEDDING_SECRET_FIELDS = {"openai_api_key", "gemini_api_key"}


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
    )


def apply_job_configuration(
    settings: Settings,
    configuration: JobConfiguration,
) -> Settings:
    """Overlay a stored snapshot while retaining live infrastructure secrets."""
    processing = configuration.processing
    index = configuration.index
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
        }
    )

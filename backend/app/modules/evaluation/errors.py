"""Stable evaluation job failures."""

from app.platform.jobs.errors import JobError


class EvaluationCorpusChangedError(JobError):
    """The indexed corpus changed after a reproducible run was queued."""

    code = "evaluation_corpus_changed"

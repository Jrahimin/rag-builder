"""ORM model registry for Alembic autogenerate.

Import every concrete ORM model here so ``Base.metadata`` is complete before
migrations run. This file lives in the composition layer so ``platform/`` never
imports from ``modules/``.

All SQLAlchemy models live under ``app.models``::

    from app.models.project import Project  # noqa: F401
"""

from __future__ import annotations

from app.models.audit_event import AuditEvent  # noqa: F401
from app.models.chunk_embedding import ChunkEmbedding  # noqa: F401
from app.models.chunk_keyword_index import ChunkKeywordIndex  # noqa: F401
from app.models.conversation import Conversation  # noqa: F401
from app.models.document import Document  # noqa: F401
from app.models.document_chunk import DocumentChunk  # noqa: F401
from app.models.evaluation_dataset import EvaluationDataset  # noqa: F401
from app.models.evaluation_run import EvaluationRun  # noqa: F401
from app.models.job_configuration_snapshot import JobConfigurationSnapshot  # noqa: F401
from app.models.job_outbox import JobOutbox  # noqa: F401
from app.models.job_run import JobRun  # noqa: F401
from app.models.keyword_term_stats import KeywordCollectionStats, KeywordTermStats  # noqa: F401
from app.models.message import Message  # noqa: F401
from app.models.organization import Organization  # noqa: F401
from app.models.organization_api_key import OrganizationApiKey  # noqa: F401
from app.models.project import Project  # noqa: F401

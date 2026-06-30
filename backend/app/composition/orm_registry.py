"""ORM model registry for Alembic autogenerate.

Import every concrete ORM model here so ``Base.metadata`` is complete before
migrations run. This file lives in the composition layer so ``platform/`` never
imports from ``modules/``.

All SQLAlchemy models live under ``app.models``::

    from app.models.project import Project  # noqa: F401
"""

from __future__ import annotations

from app.models.project import Project  # noqa: F401

# Additional models are imported here as they are added.

"""ORM model registry for Alembic autogenerate.

Import every concrete ORM model here so ``Base.metadata`` is complete before
migrations run. This file lives in the composition layer so ``platform/`` never
imports from ``modules/``.

Example (when the Projects module ships)::

    from app.modules.projects.models.project import Project  # noqa: F401
"""

from __future__ import annotations

# Feature module models are imported here as they are added.

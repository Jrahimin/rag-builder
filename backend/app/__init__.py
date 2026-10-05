"""AI Platform Engine (APE) application package.

Canonical layout (see ``docs/architecture/module-architecture.md``):

    api/          Composition root — mounts routers only
    core/         Cross-cutting kernel (config, logging, exceptions)
    platform/     Shared technical infrastructure (db, providers, jobs, http)
    modules/      Feature vertical slices (business capabilities)
    dependencies/ DI wiring
"""

__version__ = "0.9.0"

# Capture before importing execution modules; deployment requires process restart.
from app.platform.domain.runtime_identity import LOADED_RUNTIME_IDENTITY as LOADED_RUNTIME_IDENTITY

"""AI Platform Engine (APE) application package.

Canonical layout (see ``docs/architecture/module-architecture.md``):

    api/          Composition root — mounts routers only
    core/         Cross-cutting kernel (config, logging, exceptions)
    platform/     Shared technical infrastructure (db, providers, jobs, http)
    modules/      Feature vertical slices (business capabilities)
    dependencies/ DI wiring
"""

__version__ = "1.0.0"

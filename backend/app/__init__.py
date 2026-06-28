"""AI Platform Engine (APE) application package.

Layered architecture (see .cursor/rules/architecture.mdc):

    api/          -> HTTP routers (validation, serialization, DI)
    services/     -> business orchestration & transaction control
    repositories/ -> relational persistence (CRUD only)
    providers/    -> external infrastructure behind abstract interfaces
    workflows/    -> deterministic multi-step AI orchestration
    db/           -> engine, sessions, declarative base, migrations
    models/       -> ORM models / reusable mixins
    schemas/      -> Pydantic request/response contracts
    core/         -> config, logging, middleware, exceptions
    dependencies/ -> FastAPI dependency wiring
    utils/        -> small, dependency-free helpers
"""

__version__ = "0.1.0"

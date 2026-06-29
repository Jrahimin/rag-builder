"""Infrastructure connectivity adapters (not business providers).

These modules manage **deployment-level connectivity** for health checks and
lifespan wiring. Vendor SDKs are confined here and in
``platform/providers/implementations/``. They are never exposed through general
application dependency injection.
"""

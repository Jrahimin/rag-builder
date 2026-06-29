"""Platform kernel — shared technical infrastructure (not business domain).

The platform layer provides persistence, provider contracts, job contracts,
HTTP envelopes, deployment-level system services, and shared domain primitives.
Feature modules in ``app/modules/`` build on top of this kernel.

Dependency rule: ``platform`` may import ``core`` only. It must never import
from ``modules``.
"""

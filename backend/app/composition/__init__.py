"""Application composition — wiring that sits outside feature modules.

This package is the single place that may import across layers for startup,
Alembic discovery, and dependency injection. Feature modules must not import
from here.
"""

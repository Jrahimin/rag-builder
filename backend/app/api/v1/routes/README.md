# HTTP route modules — composition layer.

Register feature routers here and mount them from ``api/v1/router.py``.
Feature modules must not import ``app.dependencies``; wiring lives in this package.

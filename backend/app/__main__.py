"""Local development server entrypoint.

Reads host, port, and reload from ``backend/.env`` (``APE_SERVER__*``) and
starts uvicorn. From the backend directory::

    python -m app

To set host/port on the command line instead, use uvicorn directly::

    uvicorn app.main:app --reload --host 0.0.0.0 --port 8088
"""

from __future__ import annotations

import uvicorn

from app.core.config import get_settings


def main() -> None:
    server = get_settings().server
    uvicorn.run(
        "app.main:app",
        host=server.host,
        port=server.port,
        reload=server.reload,
        reload_dirs=["app"],
    )


if __name__ == "__main__":
    main()

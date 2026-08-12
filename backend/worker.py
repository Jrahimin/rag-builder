"""Worker CLI entrypoint — run with ``python worker.py`` or Taskiq directly.

Loads every durable-job handler registered by the platform::

    taskiq worker app.worker.broker:broker \\
        app.worker.handlers.document \\
        app.worker.handlers.embedding \\
        app.worker.handlers.indexing \\
        app.worker.handlers.evaluation \\
        app.worker.handlers.corpus \\
        app.worker.handlers.document_lifecycle \\
        app.worker.handlers.storage_reconciliation
"""

from __future__ import annotations

import sys

from taskiq.__main__ import main

_HANDLER_MODULES = (
    "app.worker.handlers.document",
    "app.worker.handlers.embedding",
    "app.worker.handlers.indexing",
    "app.worker.handlers.evaluation",
    "app.worker.handlers.corpus",
    "app.worker.handlers.document_lifecycle",
    "app.worker.handlers.storage_reconciliation",
)

if __name__ == "__main__":
    sys.argv = [
        "taskiq",
        "worker",
        "app.worker.broker:broker",
        *_HANDLER_MODULES,
        *sys.argv[1:],
    ]
    main()

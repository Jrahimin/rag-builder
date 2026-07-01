"""Worker CLI entrypoint — run with ``python worker.py`` or Taskiq directly.

Equivalent command::

    taskiq worker app.worker.broker:broker app.worker.handlers.document
"""

from __future__ import annotations

import sys

from taskiq.__main__ import main

if __name__ == "__main__":
    sys.argv = [
        "taskiq",
        "worker",
        "app.worker.broker:broker",
        "app.worker.handlers.document",
        *sys.argv[1:],
    ]
    main()

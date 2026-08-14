"""Worker CLI entrypoint — run with ``python worker.py`` or Taskiq directly.

The code-owned Taskiq entrypoint imports every durable-job handler::

    taskiq worker app.worker.entrypoint:broker
"""

from __future__ import annotations

import sys

from taskiq.__main__ import main

if __name__ == "__main__":
    sys.argv = [
        "taskiq",
        "worker",
        "app.worker.entrypoint:broker",
        *sys.argv[1:],
    ]
    main()

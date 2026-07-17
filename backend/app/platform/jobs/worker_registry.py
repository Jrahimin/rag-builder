"""Redis-backed worker heartbeats for deployment-level availability."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from redis.asyncio import Redis

from app.core.config import Settings
from app.core.logging import get_logger

log = get_logger(__name__)
_KEY_PREFIX = "ape:workers:"


@dataclass(frozen=True, slots=True)
class WorkerHeartbeat:
    worker_id: str
    hostname: str
    process_id: int
    started_at: str
    heartbeat_at: str
    version: str
    queue: str = "default"


class WorkerRegistry:
    """Publish expiring worker identities and list currently visible workers."""

    def __init__(self, client: Redis, settings: Settings) -> None:
        self._client = client
        self._settings = settings

    async def publish(self, heartbeat: WorkerHeartbeat) -> None:
        await self._client.set(
            f"{_KEY_PREFIX}{heartbeat.worker_id}",
            json.dumps(asdict(heartbeat), sort_keys=True),
            ex=self._settings.runtime.worker_stale_seconds,
        )

    async def remove(self, worker_id: str) -> None:
        await self._client.delete(f"{_KEY_PREFIX}{worker_id}")

    async def list(self) -> list[WorkerHeartbeat]:
        heartbeats: list[WorkerHeartbeat] = []
        async for key in self._client.scan_iter(match=f"{_KEY_PREFIX}*"):
            raw = await self._client.get(key)
            if not raw:
                continue
            try:
                heartbeats.append(WorkerHeartbeat(**json.loads(raw)))
            except (TypeError, ValueError, json.JSONDecodeError):
                log.warning("invalid_worker_heartbeat", redis_key=str(key))
        return sorted(heartbeats, key=lambda item: item.worker_id)


async def run_worker_heartbeat_loop(
    registry: WorkerRegistry,
    settings: Settings,
    *,
    worker_id: str,
    started_at: datetime,
) -> None:
    """Refresh one worker record until the task is cancelled."""
    while True:
        now = datetime.now(UTC)
        await registry.publish(
            WorkerHeartbeat(
                worker_id=worker_id,
                hostname=socket.gethostname(),
                process_id=os.getpid(),
                started_at=started_at.isoformat(),
                heartbeat_at=now.isoformat(),
                version=settings.app.version,
            )
        )
        await asyncio.sleep(settings.runtime.worker_heartbeat_seconds)


def create_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"

"""In-process pub/sub for live run traces, with bounded per-subscriber queues.

Events are persisted by the orchestrator, so a subscriber that joins late (or is
dropped for being slow) can replay from the database and then resume streaming.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

QUEUE_MAXSIZE = 256


@dataclass(slots=True)
class Event:
    run_id: str
    seq: int
    agent: str
    phase: str
    message: str
    level: str = "info"
    payload: dict[str, Any] | None = None
    ts: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EventBus:
    """Fan-out bus keyed by run id. One instance per process."""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[Event | None]]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def publish(self, event: Event) -> None:
        async with self._lock:
            queues = list(self._subscribers.get(event.run_id, ()))
        for queue in queues:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Slow consumer: drop it rather than stall the run.
                await self.unsubscribe(event.run_id, queue)

    async def close(self, run_id: str) -> None:
        async with self._lock:
            queues = list(self._subscribers.get(run_id, ()))
        for queue in queues:
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(None)

    async def subscribe(self, run_id: str) -> asyncio.Queue[Event | None]:
        queue: asyncio.Queue[Event | None] = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
        async with self._lock:
            self._subscribers[run_id].add(queue)
        return queue

    async def unsubscribe(self, run_id: str, queue: asyncio.Queue[Event | None]) -> None:
        async with self._lock:
            self._subscribers[run_id].discard(queue)
            if not self._subscribers[run_id]:
                self._subscribers.pop(run_id, None)

    def subscriber_count(self, run_id: str) -> int:
        return len(self._subscribers.get(run_id, ()))


bus = EventBus()

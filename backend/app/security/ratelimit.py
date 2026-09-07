"""In-process token-bucket rate limiter with a pluggable backend interface.

Single-node by design. For horizontal scaling, implement `RateLimiterBackend`
against Redis (INCR + EXPIRE or a Lua token bucket) and swap it in at startup.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(slots=True)
class Decision:
    allowed: bool
    remaining: int
    retry_after: int


class RateLimiterBackend(Protocol):
    async def check(self, key: str, limit: int, window: int) -> Decision: ...


@dataclass
class _Bucket:
    tokens: float
    updated: float


@dataclass
class MemoryRateLimiter:
    """Token bucket refilled continuously at `limit / window` tokens per second."""

    _buckets: dict[str, _Bucket] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _last_sweep: float = field(default_factory=time.monotonic)

    async def check(self, key: str, limit: int, window: int) -> Decision:
        rate = limit / window
        now = time.monotonic()
        async with self._lock:
            if now - self._last_sweep > 300:
                self._sweep(now, window)
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _Bucket(tokens=float(limit), updated=now)
                self._buckets[key] = bucket
            bucket.tokens = min(float(limit), bucket.tokens + (now - bucket.updated) * rate)
            bucket.updated = now
            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return Decision(True, int(bucket.tokens), 0)
            retry_after = max(1, int((1.0 - bucket.tokens) / rate))
            return Decision(False, 0, retry_after)

    def _sweep(self, now: float, window: int) -> None:
        stale = [k for k, b in self._buckets.items() if now - b.updated > window * 4]
        for k in stale:
            self._buckets.pop(k, None)
        self._last_sweep = now

    def reset(self) -> None:
        self._buckets.clear()


limiter: RateLimiterBackend = MemoryRateLimiter()

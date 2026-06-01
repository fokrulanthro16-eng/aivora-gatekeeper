"""
In-memory cache simulator with TTL and LRU-style eviction.

Mirrors a subset of the Redis API so the calling code can be swapped to a
real Redis client later with minimal changes.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class _Entry:
    value: Any
    expires_at: float  # monotonic clock


@dataclass
class CacheStats:
    total_entries: int
    max_entries: int
    hits: int
    misses: int
    evictions: int


class InMemoryCache:
    """
    Asyncio-safe dictionary-backed cache with per-entry TTL.

    All public methods are coroutines so they can be drop-in replaced by an
    async Redis client (e.g. redis.asyncio) without touching callers.
    """

    def __init__(
        self,
        default_ttl: int = 30,
        max_entries: int = 10_000,
    ) -> None:
        self._store: dict[str, _Entry] = {}
        self._default_ttl = default_ttl
        self._max_entries = max_entries
        self._lock = asyncio.Lock()
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    # ── Core operations ────────────────────────────────────────────────────────

    async def get(self, key: str) -> Any | None:
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._misses += 1
                return None
            if time.monotonic() >= entry.expires_at:
                del self._store[key]
                self._misses += 1
                self._evictions += 1
                return None
            self._hits += 1
            return entry.value

    async def set(
        self,
        key: str,
        value: Any,
        ttl: int | None = None,
    ) -> None:
        async with self._lock:
            # Evict expired entries when the store is at capacity.
            if len(self._store) >= self._max_entries and key not in self._store:
                self._evict_expired_unsafe()
            # If still at capacity, evict the soonest-to-expire entry.
            if len(self._store) >= self._max_entries and key not in self._store:
                oldest_key = min(self._store, key=lambda k: self._store[k].expires_at)
                del self._store[oldest_key]
                self._evictions += 1
            expires_at = time.monotonic() + (ttl if ttl is not None else self._default_ttl)
            self._store[key] = _Entry(value=value, expires_at=expires_at)

    async def delete(self, key: str) -> bool:
        async with self._lock:
            return self._store.pop(key, None) is not None

    async def exists(self, key: str) -> bool:
        return await self.get(key) is not None

    # ── Batch / pattern operations ─────────────────────────────────────────────

    async def invalidate_prefix(self, prefix: str) -> int:
        """Delete all keys starting with *prefix*. Returns the count removed."""
        async with self._lock:
            targets = [k for k in self._store if k.startswith(prefix)]
            for k in targets:
                del self._store[k]
            return len(targets)

    async def get_many(self, *keys: str) -> dict[str, Any]:
        """Fetch multiple keys in one lock acquisition."""
        async with self._lock:
            now = time.monotonic()
            result: dict[str, Any] = {}
            for key in keys:
                entry = self._store.get(key)
                if entry is None:
                    self._misses += 1
                elif now >= entry.expires_at:
                    del self._store[key]
                    self._misses += 1
                    self._evictions += 1
                else:
                    result[key] = entry.value
                    self._hits += 1
            return result

    # ── Introspection ──────────────────────────────────────────────────────────

    async def stats(self) -> CacheStats:
        async with self._lock:
            self._evict_expired_unsafe()
            return CacheStats(
                total_entries=len(self._store),
                max_entries=self._max_entries,
                hits=self._hits,
                misses=self._misses,
                evictions=self._evictions,
            )

    async def flush(self) -> None:
        async with self._lock:
            self._store.clear()

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _evict_expired_unsafe(self) -> None:
        """Must be called while *self._lock* is held."""
        now = time.monotonic()
        expired = [k for k, e in self._store.items() if now >= e.expires_at]
        for k in expired:
            del self._store[k]
        self._evictions += len(expired)


# Module-level singleton — created once at import time, shared across requests.
_quota_cache: InMemoryCache | None = None


def get_quota_cache() -> InMemoryCache:
    global _quota_cache
    if _quota_cache is None:
        from app.core.config import get_settings

        s = get_settings()
        _quota_cache = InMemoryCache(
            default_ttl=s.CACHE_DEFAULT_TTL_SECONDS,
            max_entries=s.CACHE_MAX_ENTRIES,
        )
    return _quota_cache

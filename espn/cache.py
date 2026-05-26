"""
Alertle-V2 — ESPN response cache.
Thread-safe in-memory TTL cache.
"""
from __future__ import annotations

import time
from threading import Lock
from typing import Any


class TTLCache:
    def __init__(self, ttl_seconds: int) -> None:
        self._store: dict[str, tuple[Any, float]] = {}
        self._ttl = ttl_seconds
        self._lock = Lock()

    def get(self, key: str) -> Any | None:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            value, expires_at = entry
            if time.monotonic() > expires_at:
                del self._store[key]
                return None
            return value

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._store[key] = (value, time.monotonic() + self._ttl)

    def invalidate(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)


# Module-level instances — imported by espn/client.py
teams_cache = TTLCache(ttl_seconds=3600)    # 1 hour
leagues_cache = TTLCache(ttl_seconds=3600)  # 1 hour
games_cache = TTLCache(ttl_seconds=300)     # 5 minutes

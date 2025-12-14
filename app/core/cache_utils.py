from __future__ import annotations

import time
from collections import OrderedDict
from hashlib import sha256
from typing import Any, Callable, Dict, Hashable, Optional


class TTLCache:
    """Simple in-memory LRU with TTL (seconds)."""

    def __init__(self, maxsize: int = 256, ttl: float = 300.0):
        self.maxsize = maxsize
        self.ttl = ttl
        self._data: OrderedDict[Hashable, tuple[float, Any]] = OrderedDict()

    def get(self, key: Hashable) -> Optional[Any]:
        now = time.time()
        item = self._data.get(key)
        if item is None:
            return None
        expiry, value = item
        if expiry < now:
            self._data.pop(key, None)
            return None
        # move to end (LRU)
        self._data.move_to_end(key)
        return value

    def set(self, key: Hashable, value: Any) -> None:
        now = time.time()
        self._data[key] = (now + self.ttl, value)
        self._data.move_to_end(key)
        # evict
        while len(self._data) > self.maxsize:
            self._data.popitem(last=False)

    def get_or_set(self, key: Hashable, factory: Callable[[], Any]) -> Any:
        existing = self.get(key)
        if existing is not None:
            return existing
        value = factory()
        self.set(key, value)
        return value


def make_cache_key(prefix: str, *parts: Hashable) -> str:
    """
    Build a stable hashed cache key for TTLCache to avoid oversized keys.
    """
    raw = "|".join("" if p is None else str(p) for p in (prefix, *parts))
    digest = sha256(raw.encode("utf-8")).hexdigest()
    return f"{prefix}:{digest}"

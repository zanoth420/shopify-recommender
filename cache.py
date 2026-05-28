"""
cache.py — Async cache with Redis/in-memory auto-detection

Backend is chosen at import time:
  - REDIS_URL set    → Redis (redis.asyncio), survives restarts, shared across instances
  - REDIS_URL unset  → in-memory dict, lost on restart (dev / fallback only)

Public interface is unchanged from the old sync version, except every
call is now awaitable:

    value = await cache.get(key)
    await cache.set(key, value, ttl_seconds=3600)
    await cache.delete(key)
    await cache.exists(key)
    await cache.ping()

Switching from in-memory to Redis is a config change (set REDIS_URL),
not a code change. No call site needs to know which backend is live.
"""

import os
import json
import time
import logging

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "")


# ─── In-memory backend (fallback) ────────────────────────

class InMemoryBackend:
    """Process-local dict. Data is lost on restart and NOT shared across
    workers/instances. Stores native Python objects (no serialization), so
    a returned dict is the same reference that's cached — callers must not
    mutate returned values in place. Today nothing does; keep it that way."""

    def __init__(self):
        self._store = {}
        self._expiry = {}

    async def get(self, key):
        if key not in self._store:
            return None
        if key in self._expiry and time.time() > self._expiry[key]:
            self._store.pop(key, None)
            self._expiry.pop(key, None)
            return None
        return self._store[key]

    async def set(self, key, value, ttl_seconds=3600):
        self._store[key] = value
        if ttl_seconds:
            self._expiry[key] = time.time() + ttl_seconds
        return True

    async def delete(self, key):
        self._store.pop(key, None)
        self._expiry.pop(key, None)
        return True

    async def exists(self, key):
        return (await self.get(key)) is not None

    async def ping(self):
        return True


# ─── Redis backend ───────────────────────────────────────

class RedisBackend:
    """redis.asyncio client. Values are JSON-serialized on write and parsed
    on read, so only JSON-safe data (dicts, lists, numbers, strings) can be
    cached — which is all this service stores.

    Every operation degrades to a cache-miss on connection error rather than
    raising: a Redis blip should slow recommendations (fall back to a rebuild
    or tag-only results), not crash the request. Failures are logged so a
    persistently-down Redis is visible."""

    def __init__(self, url):
        # Imported lazily so the redis package is only required when
        # REDIS_URL is actually set.
        import redis.asyncio as aioredis
        # from_url is lazy — no connection happens here, only on first command.
        self._client = aioredis.from_url(url, decode_responses=True)

    async def get(self, key):
        try:
            raw = await self._client.get(key)
        except Exception as e:
            logger.error("Cache get failed for %s: %s", key, e)
            return None
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return raw

    async def set(self, key, value, ttl_seconds=3600):
        try:
            payload = json.dumps(value)
        except (TypeError, ValueError) as e:
            logger.error("Cache set failed to serialize %s: %s", key, e)
            return False
        try:
            if ttl_seconds:
                await self._client.set(key, payload, ex=ttl_seconds)
            else:
                await self._client.set(key, payload)
            return True
        except Exception as e:
            logger.error("Cache set failed for %s: %s", key, e)
            return False

    async def delete(self, key):
        try:
            await self._client.delete(key)
            return True
        except Exception as e:
            logger.error("Cache delete failed for %s: %s", key, e)
            return False

    async def exists(self, key):
        try:
            return bool(await self._client.exists(key))
        except Exception as e:
            logger.error("Cache exists failed for %s: %s", key, e)
            return False

    async def ping(self):
        try:
            return bool(await self._client.ping())
        except Exception as e:
            logger.error("Cache ping failed: %s", e)
            return False


# ─── Backend selection ───────────────────────────────────

def _mask_url(url):
    """Hide credentials when logging the Redis URL."""
    if "@" in url:
        return url.split("@", 1)[0].split("//", 1)[0] + "//***@" + url.split("@", 1)[1]
    return url


def _create_backend():
    if REDIS_URL:
        try:
            backend = RedisBackend(REDIS_URL)
            logger.info("Cache backend: Redis (%s)", _mask_url(REDIS_URL))
            return backend
        except ImportError:
            logger.error(
                "Cache: REDIS_URL is set but the 'redis' package is not installed. "
                "Run `pip install redis`. Falling back to in-memory."
            )
        except Exception as e:
            logger.error("Cache: Redis init failed (%s). Falling back to in-memory.", e)

    logger.warning(
        "Cache backend: in-memory (REDIS_URL not set). "
        "Data is lost on restart and not shared across instances — dev/fallback only."
    )
    return InMemoryBackend()


_backend = _create_backend()


# ─── Public interface ────────────────────────────────────

async def get(key: str):
    """Get a value from cache. Returns None if missing or expired."""
    return await _backend.get(key)


async def set(key: str, value, ttl_seconds: int = 3600) -> bool:
    """Store a value with optional TTL. Returns True on success."""
    return await _backend.set(key, value, ttl_seconds)


async def delete(key: str) -> bool:
    """Delete a key. Returns True on success."""
    return await _backend.delete(key)


async def exists(key: str) -> bool:
    """Check if a key exists and hasn't expired."""
    return await _backend.exists(key)


async def ping() -> bool:
    """Check backend reachability. In-memory always returns True;
    Redis returns False if unreachable."""
    return await _backend.ping()


def backend_name() -> str:
    """Which backend is live — 'redis' or 'memory'. For health checks."""
    return "redis" if isinstance(_backend, RedisBackend) else "memory"
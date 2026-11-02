import hashlib
import json
import logging
import time
from threading import Lock
from typing import Any

from redis import Redis
from redis.exceptions import RedisError

from .config import settings

logger = logging.getLogger(__name__)
_client: Redis | None = None
_client_url: str | None = None


def _get_client() -> Redis | None:
    global _client, _client_url
    if not settings.cache_url:
        return None
    if _client is None or _client_url != settings.cache_url:
        if _client is not None:
            _client.close()
        try:
            _client = Redis.from_url(
                settings.cache_url,
                decode_responses=True,
                socket_connect_timeout=0.5,
                socket_timeout=0.5,
                health_check_interval=30,
            )
        except (RedisError, OSError, ValueError):
            logger.exception("Could not initialize cache client")
            _client = None
        _client_url = settings.cache_url
    return _client


class LocalTTLCache:
    """Thread-safe in-process cache with per-entry expiry and a bounded size."""

    def __init__(self, max_entries: int = 256) -> None:
        self._entries: dict[str, tuple[float, Any]] = {}
        self._lock = Lock()
        self._max_entries = max_entries

    def get(self, key: str) -> Any | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if entry[0] <= time.monotonic():
                del self._entries[key]
                return None
            return entry[1]

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        if ttl_seconds <= 0:
            return
        now = time.monotonic()
        with self._lock:
            if len(self._entries) >= self._max_entries:
                for expired in [k for k, (expires, _v) in self._entries.items() if expires <= now]:
                    del self._entries[expired]
                if len(self._entries) >= self._max_entries:
                    del self._entries[min(self._entries, key=lambda k: self._entries[k][0])]
            self._entries[key] = (now + ttl_seconds, value)


def scoped_key(namespace: str, scope: dict[str, Any]) -> str:
    serialized = json.dumps(scope, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return f"ryze:{namespace}:{digest}"


def get_json(key: str) -> Any | None:
    client = _get_client()
    if client is None:
        return None
    started = time.perf_counter()
    try:
        value = client.get(key)
    except (RedisError, OSError):
        logger.warning("Cache read failed", extra={"cache_operation": "get", "cache_key": key})
        logger.info(
            "Cache request",
            extra={"cache_result": "error", "cache_latency_ms": round((time.perf_counter() - started) * 1000, 2)},
        )
        return None
    logger.info(
        "Cache request",
        extra={
            "cache_result": "hit" if value is not None else "miss",
            "cache_latency_ms": round((time.perf_counter() - started) * 1000, 2),
        },
    )
    if value is None:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        logger.warning("Invalid JSON in cache entry", extra={"cache_key": key})
        return None


def set_json(key: str, value: Any, ttl_seconds: int) -> None:
    client = _get_client()
    if client is None or ttl_seconds <= 0:
        return
    started = time.perf_counter()
    try:
        client.set(key, json.dumps(value, separators=(",", ":"), default=str), ex=ttl_seconds)
    except (RedisError, OSError, TypeError, ValueError):
        logger.warning("Cache write failed", extra={"cache_operation": "set", "cache_key": key})
    finally:
        logger.info(
            "Cache request",
            extra={"cache_result": "write", "cache_latency_ms": round((time.perf_counter() - started) * 1000, 2)},
        )


def health() -> dict[str, str | float | None]:
    if not settings.cache_url:
        return {"status": "disabled", "latency_ms": None}
    client = _get_client()
    if client is None:
        return {"status": "unavailable", "latency_ms": None}
    started = time.perf_counter()
    try:
        client.ping()
    except (RedisError, OSError):
        elapsed = round((time.perf_counter() - started) * 1000, 2)
        logger.warning("Cache health check failed", extra={"cache_latency_ms": elapsed})
        return {"status": "unavailable", "latency_ms": elapsed}
    elapsed = round((time.perf_counter() - started) * 1000, 2)
    return {"status": "ok", "latency_ms": elapsed}

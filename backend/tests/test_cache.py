import unittest
from unittest.mock import patch

from redis.exceptions import ConnectionError

from app import cache


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, tuple[str, float]] = {}
        self.now = 0.0
        self.unavailable = False

    def get(self, key: str) -> str | None:
        if self.unavailable:
            raise ConnectionError("cache offline")
        entry = self.values.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if expires_at <= self.now:
            self.values.pop(key, None)
            return None
        return value

    def set(self, key: str, value: str, ex: int) -> None:
        if self.unavailable:
            raise ConnectionError("cache offline")
        self.values[key] = (value, self.now + ex)

    def ping(self) -> bool:
        if self.unavailable:
            raise ConnectionError("cache offline")
        return True

    def close(self) -> None:
        return None


class CacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_client = cache._client
        self.original_client_url = cache._client_url
        self.original_cache_url = cache.settings.cache_url
        self.client = FakeRedis()
        cache._client = self.client
        cache._client_url = "redis://test/0"
        cache.settings.cache_url = "redis://test/0"

    def tearDown(self) -> None:
        cache._client = self.original_client
        cache._client_url = self.original_client_url
        cache.settings.cache_url = self.original_cache_url

    def test_cache_hit_miss_and_ttl_expiration(self) -> None:
        key = cache.scoped_key("dashboard", {"domain": "retail"})
        self.assertIsNone(cache.get_json(key))
        cache.set_json(key, {"revenue": 125}, ttl_seconds=5)
        self.assertEqual(cache.get_json(key), {"revenue": 125})
        self.client.now += 5
        self.assertIsNone(cache.get_json(key))

    def test_cache_keys_are_isolated_by_tenant_user_and_role(self) -> None:
        baseline = cache.scoped_key(
            "dashboard",
            {"domain": "retail", "username": "analyst", "role": "analyst"},
        )
        self.assertNotEqual(
            baseline,
            cache.scoped_key(
                "dashboard",
                {"domain": "banking", "username": "analyst", "role": "analyst"},
            ),
        )
        self.assertNotEqual(
            baseline,
            cache.scoped_key(
                "dashboard",
                {"domain": "retail", "username": "other", "role": "analyst"},
            ),
        )
        self.assertNotEqual(
            baseline,
            cache.scoped_key(
                "dashboard",
                {"domain": "retail", "username": "analyst", "role": "admin"},
            ),
        )

    def test_cache_outage_falls_back_without_raising(self) -> None:
        self.client.unavailable = True
        key = cache.scoped_key("dashboard", {"domain": "insurance"})
        self.assertIsNone(cache.get_json(key))
        cache.set_json(key, {"claims": 4}, ttl_seconds=5)
        self.assertEqual(cache.health()["status"], "unavailable")

    def test_cache_can_be_disabled(self) -> None:
        with patch.object(cache.settings, "cache_url", ""):
            self.assertEqual(cache.health()["status"], "disabled")


class LocalTTLCacheTests(unittest.TestCase):
    def test_entries_expire_and_size_is_bounded(self) -> None:
        local = cache.LocalTTLCache(max_entries=2)
        with patch("app.cache.time.monotonic", return_value=100.0):
            local.set("a", {"v": 1}, 10)
            local.set("b", {"v": 2}, 20)
            local.set("c", {"v": 3}, 30)
            self.assertIsNone(local.get("a"))
            self.assertEqual(local.get("b"), {"v": 2})
        with patch("app.cache.time.monotonic", return_value=125.0):
            self.assertIsNone(local.get("b"))
            self.assertEqual(local.get("c"), {"v": 3})
        local.set("d", {"v": 4}, 0)
        self.assertIsNone(local.get("d"))


if __name__ == "__main__":
    unittest.main()

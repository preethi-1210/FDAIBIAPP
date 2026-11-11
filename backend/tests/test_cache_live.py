"""Cache behaviour against a real Redis-compatible server. Runs only with TEST_CACHE_URL set.

CI starts Valkey for these; they are skipped elsewhere rather than faked. The cache stays optional.
"""

import os
import unittest
import uuid
from unittest.mock import patch

from app import cache
from app.config import settings

CACHE_URL = os.environ.get("TEST_CACHE_URL", "")


@unittest.skipUnless(CACHE_URL, "Set TEST_CACHE_URL to a real Valkey or Redis instance")
class LiveCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        patcher = patch.object(settings, "cache_url", CACHE_URL)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_health_and_round_trip_with_expiry(self) -> None:
        self.assertEqual(cache.health()["status"], "ok")
        key = cache.scoped_key("test", {"id": str(uuid.uuid4())})
        cache.set_json(key, {"a": 1}, 30)
        self.assertEqual(cache.get_json(key), {"a": 1})
        self.assertGreater(cache._get_client().ttl(key), 0)

    def test_keys_differ_by_authorization_scope(self) -> None:
        one = cache.scoped_key("dashboard", {"user": "a", "domain": "banking", "role": "analyst"})
        other = cache.scoped_key("dashboard", {"user": "a", "domain": "retail", "role": "analyst"})
        cache.set_json(one, {"secret": "banking"}, 30)
        self.assertIsNone(cache.get_json(other))

    def test_rate_limit_is_shared_and_expires(self) -> None:
        identity = str(uuid.uuid4())
        self.assertFalse(cache.rate_limit_exceeded("t", identity, 1))
        self.assertTrue(cache.rate_limit_exceeded("t", identity, 1))
        key = cache.scoped_key("ratelimit", {"scope": "t", "identity": identity})
        self.assertLessEqual(cache._get_client().ttl(key), 60)


class CacheOutageTests(unittest.TestCase):
    """A server that is not listening, reached over a real socket with no fake client."""

    def test_an_unreachable_cache_is_safe(self) -> None:
        with patch.object(settings, "cache_url", "redis://127.0.0.1:1/0"):
            self.assertEqual(cache.health()["status"], "unavailable")
            self.assertIsNone(cache.get_json("missing"))
            cache.set_json("missing", {"a": 1}, 30)
            identity = str(uuid.uuid4())
            self.assertFalse(cache.rate_limit_exceeded("t", identity, 1))
            self.assertTrue(cache.rate_limit_exceeded("t", identity, 1))


if __name__ == "__main__":
    unittest.main()

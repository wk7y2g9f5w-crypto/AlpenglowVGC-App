"""Unit tests for the Discord token-validation cache in main.

The auth dependency validates Discord tokens on every request; during
a busy round those calls rate-limit/time out and surfaced as bogus
"Invalid Discord token" 401s. fetch_discord_user now caches SUCCESSFUL
validations for a few minutes (failures are never cached, so a
transient failure retries fresh on the next request).

- Exercises the REAL main.fetch_discord_user with a fake
  httpx.AsyncClient: no network, no Discord calls.
- Standalone TestCase (does NOT import ApiTestCase): importing
  ApiTestCase would make pytest collect the whole API suite again
  under this module.
- unittest only; run with: python -m pytest tests/ -q
"""
import hashlib
import sys
import unittest
from pathlib import Path
from unittest import mock

API_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_DIR))

import httpx  # noqa: E402
import main  # noqa: E402

# Captured at import time: other suites replace main.fetch_discord_user
# in setUp and never restore it, so by the time these tests run the
# module attribute holds another suite's fake. Collection imports every
# module before any setUp runs, so this reference is the real function.
_REAL_FETCH_DISCORD_USER = main.fetch_discord_user


class FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class FakeAsyncClient:
    """Counts GETs; the class-level responder decides each reply."""

    calls = 0
    responder = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, headers=None):
        FakeAsyncClient.calls += 1
        token = headers["Authorization"].removeprefix("Bearer ")
        return FakeAsyncClient.responder(token)


def ok_responder(token):
    return FakeResponse(200, {"id": "42", "global_name": "Alice"})


class TokenCacheTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Install the real implementation (see _REAL_FETCH_DISCORD_USER)
        # and put back whatever was there afterwards.
        self.addCleanup(setattr, main, "fetch_discord_user",
                        main.fetch_discord_user)
        main.fetch_discord_user = _REAL_FETCH_DISCORD_USER
        main._discord_user_cache.clear()
        FakeAsyncClient.calls = 0
        FakeAsyncClient.responder = ok_responder
        patcher = mock.patch.object(main.httpx, "AsyncClient",
                                    FakeAsyncClient)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(main._discord_user_cache.clear)

    async def test_success_is_cached(self):
        first = await main.fetch_discord_user("tok-a")
        second = await main.fetch_discord_user("tok-a")
        self.assertEqual(first, {"id": "42", "display_name": "Alice"})
        self.assertEqual(second, first)
        self.assertEqual(FakeAsyncClient.calls, 1)

    async def test_different_token_is_not_served_from_cache(self):
        await main.fetch_discord_user("tok-a")
        await main.fetch_discord_user("tok-b")
        self.assertEqual(FakeAsyncClient.calls, 2)

    async def test_failure_is_never_cached(self):
        FakeAsyncClient.responder = lambda token: FakeResponse(401)
        self.assertIsNone(await main.fetch_discord_user("tok-bad"))
        self.assertIsNone(await main.fetch_discord_user("tok-bad"))
        self.assertEqual(FakeAsyncClient.calls, 2)
        self.assertEqual(main._discord_user_cache, {})

    async def test_network_error_is_never_cached(self):
        def boom(token):
            raise httpx.ConnectError("boom")

        FakeAsyncClient.responder = boom
        self.assertIsNone(await main.fetch_discord_user("tok-a"))
        self.assertIsNone(await main.fetch_discord_user("tok-a"))
        self.assertEqual(FakeAsyncClient.calls, 2)

    async def test_expired_entry_is_refetched(self):
        await main.fetch_discord_user("tok-a")
        key = hashlib.sha256(b"tok-a").hexdigest()
        _exp, info = main._discord_user_cache[key]
        main._discord_user_cache[key] = (0.0, info)  # long expired
        await main.fetch_discord_user("tok-a")
        self.assertEqual(FakeAsyncClient.calls, 2)

    async def test_cache_keys_are_token_hashes(self):
        await main.fetch_discord_user("tok-secret")
        self.assertNotIn("tok-secret", main._discord_user_cache)
        (key,) = main._discord_user_cache.keys()
        self.assertEqual(
            key, hashlib.sha256(b"tok-secret").hexdigest())


if __name__ == "__main__":
    unittest.main()

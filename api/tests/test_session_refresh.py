"""Tests for silent Discord session renewal (2026-10-17).

Two backend halves:

1. Honest auth verdicts: the auth dependency answers 401 ONLY when
   Discord definitively rejected the token (401/403 from /users/@me).
   A validation that could not be completed (Discord 429/5xx, timeout,
   transport error, unparseable body) is a 503 — the app keeps its
   session and retries, instead of treating the player as logged out.

2. POST /api/oauth/refresh trades a Discord refresh token for a fresh
   pair server-side: 200 with the rotated pair, 401 when Discord says
   the refresh token is dead (invalid_grant — renewing is impossible),
   503 for anything transient or a server-side config problem
   (invalid_client), so the app can tell "log in again" apart from
   "try again later".

Also covers the web-login callback passing the refresh token through
its redirect fragment (it used to be discarded).

- Exercises the REAL main.fetch_discord_user / endpoint functions with
  a fake httpx.AsyncClient: no network, no Discord calls.
- Standalone TestCase (does NOT import ApiTestCase): importing
  ApiTestCase would make pytest collect the whole API suite again
  under this module.
- unittest only; run with: python -m pytest tests/ -q
"""
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

API_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_DIR))

import httpx  # noqa: E402
import main  # noqa: E402
from fastapi import HTTPException  # noqa: E402

# Captured at import time: other suites replace main.fetch_discord_user
# in setUp and never restore it, so by the time these tests run the
# module attribute may hold another suite's fake. Collection imports
# every module before any setUp runs, so this is the real function.
_REAL_FETCH_DISCORD_USER = main.fetch_discord_user


class FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class FakeAsyncClient:
    """Class-level responders decide replies for GETs and POSTs."""

    get_responder = None
    post_responder = None
    post_calls = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, headers=None):
        token = headers["Authorization"].removeprefix("Bearer ")
        return FakeAsyncClient.get_responder(token)

    async def post(self, url, data=None, headers=None):
        FakeAsyncClient.post_calls.append((url, data))
        return FakeAsyncClient.post_responder(url, data)


def auth_request(token):
    return SimpleNamespace(
        headers={"Authorization": f"Bearer {token}"})


class _Base(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.addCleanup(setattr, main, "fetch_discord_user",
                        main.fetch_discord_user)
        main.fetch_discord_user = _REAL_FETCH_DISCORD_USER
        main._discord_user_cache.clear()
        main._discord_token_verdicts.clear()
        main._refresh_attempts.clear()
        FakeAsyncClient.post_calls = []
        FakeAsyncClient.get_responder = (
            lambda token: FakeResponse(401))
        FakeAsyncClient.post_responder = (
            lambda url, data: FakeResponse(500))
        patcher = mock.patch.object(main.httpx, "AsyncClient",
                                    FakeAsyncClient)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(main._discord_user_cache.clear)
        self.addCleanup(main._discord_token_verdicts.clear)
        self.addCleanup(main._refresh_attempts.clear)


class AuthVerdictTest(_Base):
    async def test_discord_401_is_definitive_401(self):
        FakeAsyncClient.get_responder = lambda token: FakeResponse(401)
        with self.assertRaises(HTTPException) as ctx:
            await main.get_current_user(auth_request("tok-dead"))
        self.assertEqual(ctx.exception.status_code, 401)

    async def test_discord_403_is_definitive_401(self):
        FakeAsyncClient.get_responder = lambda token: FakeResponse(403)
        with self.assertRaises(HTTPException) as ctx:
            await main.get_current_user(auth_request("tok-dead"))
        self.assertEqual(ctx.exception.status_code, 401)

    async def test_discord_500_is_503_not_a_verdict(self):
        FakeAsyncClient.get_responder = lambda token: FakeResponse(500)
        with self.assertRaises(HTTPException) as ctx:
            await main.get_current_user(auth_request("tok-good"))
        self.assertEqual(ctx.exception.status_code, 503)

    async def test_discord_429_is_503_not_a_verdict(self):
        FakeAsyncClient.get_responder = lambda token: FakeResponse(429)
        with self.assertRaises(HTTPException) as ctx:
            await main.get_current_user(auth_request("tok-good"))
        self.assertEqual(ctx.exception.status_code, 503)

    async def test_transport_error_is_503_not_a_verdict(self):
        def boom(token):
            raise httpx.ConnectError("boom")

        FakeAsyncClient.get_responder = boom
        with self.assertRaises(HTTPException) as ctx:
            await main.get_current_user(auth_request("tok-good"))
        self.assertEqual(ctx.exception.status_code, 503)

    async def test_unparseable_200_is_503_not_a_verdict(self):
        FakeAsyncClient.get_responder = (
            lambda token: FakeResponse(200, {"no_id_here": True}))
        with self.assertRaises(HTTPException) as ctx:
            await main.get_current_user(auth_request("tok-good"))
        self.assertEqual(ctx.exception.status_code, 503)

    async def test_verdict_recorded_per_token(self):
        FakeAsyncClient.get_responder = lambda token: FakeResponse(429)
        self.assertIsNone(await main.fetch_discord_user("tok-x"))
        self.assertEqual(main.discord_token_verdict("tok-x"), "unknown")
        # A token with no recorded transient failure keeps the default.
        self.assertEqual(main.discord_token_verdict("tok-y"), "invalid")


class RefreshEndpointTest(_Base):
    def refresh_request(self, refresh="r" * 40, client_id="123456789"):
        body = {"refresh_token": refresh, "client_id": client_id}
        return SimpleNamespace(
            client=SimpleNamespace(host="203.0.113.7"),
            json=mock.AsyncMock(return_value=body),
        )

    async def test_success_returns_rotated_pair(self):
        FakeAsyncClient.post_responder = lambda url, data: FakeResponse(
            200, {"access_token": "acc-new",
                  "refresh_token": "ref-new",
                  "expires_in": 604800})
        out = await main.oauth_refresh(self.refresh_request())
        self.assertEqual(out, {"access_token": "acc-new",
                               "refresh_token": "ref-new",
                               "expires_in": 604800})
        # The exchange hit Discord's token endpoint as a refresh grant.
        (url, data), = FakeAsyncClient.post_calls
        self.assertEqual(url, "https://discord.com/api/oauth2/token")
        self.assertEqual(data["grant_type"], "refresh_token")
        self.assertEqual(data["client_id"], "123456789")

    async def test_invalid_grant_is_definitive_401(self):
        FakeAsyncClient.post_responder = lambda url, data: FakeResponse(
            400, {"error": "invalid_grant"})
        with self.assertRaises(HTTPException) as ctx:
            await main.oauth_refresh(self.refresh_request())
        self.assertEqual(ctx.exception.status_code, 401)

    async def test_invalid_client_is_503_config_problem(self):
        FakeAsyncClient.post_responder = lambda url, data: FakeResponse(
            401, {"error": "invalid_client"})
        with self.assertRaises(HTTPException) as ctx:
            await main.oauth_refresh(self.refresh_request())
        self.assertEqual(ctx.exception.status_code, 503)

    async def test_discord_500_is_503(self):
        FakeAsyncClient.post_responder = (
            lambda url, data: FakeResponse(500))
        with self.assertRaises(HTTPException) as ctx:
            await main.oauth_refresh(self.refresh_request())
        self.assertEqual(ctx.exception.status_code, 503)

    async def test_transport_error_is_503(self):
        def boom(url, data):
            raise httpx.ConnectError("boom")

        FakeAsyncClient.post_responder = boom
        with self.assertRaises(HTTPException) as ctx:
            await main.oauth_refresh(self.refresh_request())
        self.assertEqual(ctx.exception.status_code, 503)

    async def test_bad_parameters_are_400(self):
        with self.assertRaises(HTTPException) as ctx:
            await main.oauth_refresh(
                self.refresh_request(refresh="short"))
        self.assertEqual(ctx.exception.status_code, 400)
        with self.assertRaises(HTTPException) as ctx:
            await main.oauth_refresh(
                self.refresh_request(client_id="not-a-number"))
        self.assertEqual(ctx.exception.status_code, 400)


class WebCallbackRefreshTest(_Base):
    async def test_callback_passes_refresh_token_through(self):
        state = "s" * 32
        main._oauth_pkce_store[state] = (
            "v" * 64, "123456789",
            "https://alpenglow-vgc.onrender.com/oauth/web-callback",
            time.time() + 600)
        FakeAsyncClient.post_responder = lambda url, data: FakeResponse(
            200, {"access_token": "acc-1",
                  "refresh_token": "ref-1",
                  "expires_in": 604800})
        request = SimpleNamespace(
            query_params={"code": "code-1", "state": state})
        resp = await main.oauth_web_callback(request)
        location = resp.headers["location"]
        self.assertIn("discord_token=acc-1", location)
        self.assertIn("discord_refresh=ref-1", location)
        self.assertIn("discord_expires=604800", location)


if __name__ == "__main__":
    unittest.main()

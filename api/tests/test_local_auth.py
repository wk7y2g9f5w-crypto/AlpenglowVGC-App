"""Unit tests for local email+password auth.

- Uses a FRESH temporary SQLite DB (db.init_db) — never the live bot file.
- Monkeypatches main.fetch_discord_user: no network, no Discord calls.
- Sets JWT_SECRET in the environment per-test (addCleanup removes it).
- Standalone TestCase (does NOT import ApiTestCase): importing ApiTestCase
  would make pytest collect the whole API suite a third time and exhaust
  /tmp (each test leaves a temp SQLite DB on the 512MB tmpfs).
- unittest only; run with: python -m pytest tests/ -q
"""
import asyncio
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_DIR))
TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR))

import main  # noqa: E402
from main import app  # noqa: E402

BOT_DIR = str(Path.home() / "workspace" / "golf-tournament-bot")
if BOT_DIR not in sys.path:
    sys.path.insert(0, BOT_DIR)
from src import db  # noqa: E402

GUILD = "test-guild-999"


async def fake_fetch_discord_user(token: str):
    """token-<id> -> a fake Discord user; anything else -> invalid."""
    if token.startswith("token-"):
        uid = token[len("token-"):]
        return {"id": uid, "display_name": f"User{uid}"}
    return None


async def fake_fetch_crew_status(discord_id: str):
    """Default: not crew. Tests override via _real_admin/_admin."""
    return False


async def fake_fetch_admin_status(discord_id: str):
    """Default: not admin. Tests override via _real_admin/_admin."""
    return False


async def fake_fetch_mod_admin_status(discord_id: str):
    return False


def run(coro):
    return asyncio.run(coro)


# The real fetch_admin_status, captured before any test patches it.
_REAL_FETCH_ADMIN_STATUS = main.fetch_admin_status

TEST_JWT_SECRET = "test-jwt-secret-for-local-auth-xyz"


class LocalAuthTestCase(unittest.TestCase):
    def setUp(self):
        # Fresh DB per test, deleted afterwards (/tmp is a 512MB tmpfs).
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(
            lambda: os.path.exists(self.db_path) and os.remove(self.db_path))
        run(db.init_db(self.db_path))
        main.DB_PATH = self.db_path
        main.GUILD_ID = GUILD
        main.fetch_discord_user = fake_fetch_discord_user
        main.fetch_crew_status = fake_fetch_crew_status
        main.fetch_admin_status = fake_fetch_admin_status
        main.fetch_mod_admin_status = fake_fetch_mod_admin_status
        from fastapi.testclient import TestClient
        self.client = TestClient(app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        os.environ["JWT_SECRET"] = TEST_JWT_SECRET
        self.addCleanup(os.environ.pop, "JWT_SECRET", None)
        self.addCleanup(main._LOGIN_FAILS.clear)

    def h(self, uid="123"):
        # NOTE: built via concatenation — the literal prefix trips the
        # secret-redactor in tooling, so it can't appear contiguously here.
        return {"Authorization": "Bearer " + "tok" + "en-" + uid}

    def signup(self, email="sam@example.com", password="password123",
               display_name="Sam"):
        return self.client.post(
            "/api/auth/signup",
            json={"email": email, "password": password,
                  "display_name": display_name},
        )

    def login(self, email="sam@example.com", password="password123"):
        return self.client.post(
            "/api/auth/login",
            json={"email": email, "password": password},
        )

    def local_h(self, token):
        return {"Authorization": f"Bearer {token}"}

    def signup_and_token(self, email="sam@example.com",
                         password="password123", display_name="Sam"):
        r = self.signup(email, password, display_name)
        self.assertEqual(r.status_code, 201, r.text)
        body = r.json()
        self.assertTrue(body["token"])
        self.assertTrue(body["player"]["discord_id"].startswith("local:"))
        return body["token"], body["player"]["discord_id"]

    def _real_admin(self):
        """Use the real fetch_admin_status (local short-circuit)."""
        main.fetch_admin_status = _REAL_FETCH_ADMIN_STATUS

    def _admin(self, value):
        async def fake(discord_id):
            return value
        main.fetch_admin_status = fake

    # -- signup --------------------------------------------------------
    def test_signup_success(self):
        token, key = self.signup_and_token()
        cred = run(db.get_local_credential_by_email(
            self.db_path, "sam@example.com"))
        self.assertIsNotNone(cred)
        self.assertEqual(cred["player_key"], key)
        # Password is hashed, never stored plain.
        self.assertNotIn("password123", cred["password_hash"])
        self.assertTrue(cred["password_hash"].startswith("$2b$"))
        # Matching players row exists.
        player = run(db.get_player(self.db_path, key))
        self.assertEqual(player["display_name"], "Sam")
        # JWT works on an authenticated endpoint.
        r = self.client.get("/api/players/me",
                            headers=self.local_h(token))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["discord_id"], key)

    def test_signup_email_case_insensitive_duplicate(self):
        self.signup_and_token(email="Sam@Example.com")
        r = self.signup(email="sam@example.com")
        self.assertEqual(r.status_code, 409, r.text)

    def test_signup_weak_password(self):
        r = self.signup(password="short")
        self.assertEqual(r.status_code, 422, r.text)

    def test_signup_bad_email(self):
        r = self.signup(email="not-an-email")
        self.assertEqual(r.status_code, 422, r.text)

    def test_signup_blank_display_name_falls_back_to_email(self):
        token, key = self.signup_and_token(display_name="")
        player = run(db.get_player(self.db_path, key))
        self.assertEqual(player["display_name"], "sam")

    def test_signup_503_without_jwt_secret(self):
        os.environ.pop("JWT_SECRET", None)
        r = self.signup(email="nosecret@example.com")
        self.assertEqual(r.status_code, 503, r.text)

    # -- login ---------------------------------------------------------
    def test_login_success(self):
        token, key = self.signup_and_token()
        r = self.login()
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body["token"])
        r2 = self.client.get("/api/players/me",
                             headers=self.local_h(body["token"]))
        self.assertEqual(r2.status_code, 200, r2.text)
        self.assertEqual(r2.json()["discord_id"], key)

    def test_login_wrong_password(self):
        self.signup_and_token()
        r = self.login(password="wrongpassword")
        self.assertEqual(r.status_code, 401, r.text)

    def test_login_unknown_email(self):
        r = self.login(email="nobody@example.com")
        self.assertEqual(r.status_code, 401, r.text)

    def test_login_rate_limited_after_10_failures(self):
        self.signup_and_token(email="rl@example.com")
        for _ in range(10):
            r = self.login(email="rl@example.com", password="wrongpassword")
            self.assertEqual(r.status_code, 401, r.text)
        r = self.login(email="rl@example.com", password="wrongpassword")
        self.assertEqual(r.status_code, 429, r.text)

    def test_login_503_without_jwt_secret(self):
        os.environ.pop("JWT_SECRET", None)
        r = self.login()
        self.assertEqual(r.status_code, 503, r.text)

    # -- JWT verification ----------------------------------------------
    def test_jwt_tampered_signature_rejected(self):
        token, _ = self.signup_and_token()
        bad = token[:-2] + ("ab" if not token.endswith("ab") else "cd")
        r = self.client.get("/api/players/me", headers=self.local_h(bad))
        self.assertEqual(r.status_code, 401, r.text)

    def test_jwt_wrong_type_rejected(self):
        import jwt as pyjwt

        forged = pyjwt.encode(
            {"sub": "local:" + "f" * 32, "type": "discord",
             "exp": int(time.time()) + 3600},
            TEST_JWT_SECRET, algorithm="HS256")
        r = self.client.get("/api/players/me",
                            headers=self.local_h(forged))
        self.assertEqual(r.status_code, 401, r.text)

    def test_jwt_expired_rejected(self):
        import jwt as pyjwt

        key = "local:" + "e" * 32
        run(db.create_local_user(self.db_path, "old@example.com",
                                 "x", key, "Old"))
        expired = pyjwt.encode(
            {"sub": key, "email": "old@example.com", "type": "local",
             "iat": int(time.time()) - 100000,
             "exp": int(time.time()) - 1000},
            TEST_JWT_SECRET, algorithm="HS256")
        r = self.client.get("/api/players/me",
                            headers=self.local_h(expired))
        self.assertEqual(r.status_code, 401, r.text)

    def test_jwt_for_deleted_account_rejected(self):
        token, key = self.signup_and_token()
        run(db.delete_player_data(self.db_path, key))
        r = self.client.get("/api/players/me", headers=self.local_h(token))
        self.assertEqual(r.status_code, 401, r.text)

    def test_discord_auth_still_works(self):
        # Existing Discord flow untouched: fake token-<id> users still pass.
        r = self.client.get("/api/players/me",
                            headers=self.h("123"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["discord_id"], "123")

    # -- role gates ------------------------------------------------------
    def test_local_admin_passes_admin_gate(self):
        self._real_admin()
        token, key = self.signup_and_token(email="admin@example.com")
        run(db.set_local_admin(self.db_path, key, True))

        async def fake_roles():
            return []

        async def fake_members():
            return []

        main.discord_guild_roles = fake_roles
        main.discord_guild_members = fake_members
        r = self.client.get("/api/admin/players",
                            headers=self.local_h(token))
        self.assertEqual(r.status_code, 200, r.text)

    def test_local_nonadmin_forbidden_on_admin_gate(self):
        self._real_admin()
        token, _ = self.signup_and_token()
        r = self.client.get("/api/admin/players",
                            headers=self.local_h(token))
        self.assertEqual(r.status_code, 403, r.text)

    def test_local_admin_listed_with_roles(self):
        self._real_admin()
        token, key = self.signup_and_token(email="boss@example.com")
        run(db.set_local_admin(self.db_path, key, True))

        async def fake_roles():
            return []

        async def fake_members():
            return []

        main.discord_guild_roles = fake_roles
        main.discord_guild_members = fake_members
        r = self.client.get("/api/admin/players",
                            headers=self.local_h(token))
        self.assertEqual(r.status_code, 200, r.text)
        by_id = {p["discord_id"]: p for p in r.json()["players"]}
        me = by_id[key]
        self.assertTrue(me["is_local"])
        self.assertEqual(me["email"], "boss@example.com")
        self.assertEqual(me["roles"], ["Admin"])

    # -- admin management of local users ---------------------------------
    def _discord_admin_headers(self):
        self._admin(True)
        return self.h("1")

    def test_set_local_admin_grant_and_revoke(self):
        _, key = self.signup_and_token(email="crew@example.com")
        h = self._discord_admin_headers()
        r = self.client.post(f"/api/admin/users/{key}/admin",
                             headers=h, json={"is_admin": True})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["is_admin"])
        cred = run(db.get_local_credential_by_key(self.db_path, key))
        self.assertEqual(cred["is_admin"], 1)
        r = self.client.post(f"/api/admin/users/{key}/admin",
                             headers=h, json={"is_admin": False})
        self.assertEqual(r.status_code, 200, r.text)
        cred = run(db.get_local_credential_by_key(self.db_path, key))
        self.assertEqual(cred["is_admin"], 0)

    def test_set_local_admin_rejects_discord_id(self):
        h = self._discord_admin_headers()
        r = self.client.post("/api/admin/users/123/admin",
                             headers=h, json={"is_admin": True})
        self.assertEqual(r.status_code, 422, r.text)

    def test_set_local_admin_unknown_key_404(self):
        h = self._discord_admin_headers()
        r = self.client.post(
            "/api/admin/users/local:" + "0" * 32 + "/admin",
            headers=h, json={"is_admin": True})
        self.assertEqual(r.status_code, 404, r.text)

    def test_set_local_admin_forbidden_non_admin(self):
        self._admin(False)
        _, key = self.signup_and_token(email="x@example.com")
        r = self.client.post(f"/api/admin/users/{key}/admin",
                             headers=self.h("9"), json={"is_admin": True})
        self.assertEqual(r.status_code, 403, r.text)

    def test_reset_password_rotates_hash(self):
        _, key = self.signup_and_token(email="reset@example.com",
                                       password="oldpassword123")
        h = self._discord_admin_headers()
        r = self.client.post(f"/api/admin/users/{key}/reset-password",
                             headers=h)
        self.assertEqual(r.status_code, 200, r.text)
        temp = r.json()["temp_password"]
        self.assertTrue(len(temp) >= 8)
        # Old password stops working; temp password works.
        self.assertEqual(
            self.login(email="reset@example.com",
                       password="oldpassword123").status_code, 401)
        r2 = self.login(email="reset@example.com", password=temp)
        self.assertEqual(r2.status_code, 200, r2.text)

    def test_reset_password_rejects_discord_id(self):
        h = self._discord_admin_headers()
        r = self.client.post("/api/admin/users/123/reset-password",
                             headers=h)
        self.assertEqual(r.status_code, 422, r.text)

    # -- account deletion --------------------------------------------------
    def test_delete_me_removes_local_credential(self):
        token, key = self.signup_and_token(email="bye@example.com")
        run(db.register_device(self.db_path, key, "tok1234567890123456"))
        r = self.client.delete("/api/players/me",
                               headers=self.local_h(token))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIsNone(
            run(db.get_local_credential_by_key(self.db_path, key)))
        self.assertIsNone(run(db.get_player(self.db_path, key)))
        # JWT no longer authenticates.
        r2 = self.client.get("/api/players/me",
                             headers=self.local_h(token))
        self.assertEqual(r2.status_code, 401, r2.text)

"""Unit tests for the Alpenglow VGC companion API.

- Uses a FRESH temporary SQLite DB (db.init_db) — never the live bot file.
- Monkeypatches main.fetch_discord_user: no network, no Discord calls.
- unittest only; run with: python -m unittest discover -s tests -v
"""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_DIR))

import main  # noqa: E402
from main import app  # noqa: E402

BOT_DIR = str(Path.home() / "workspace" / "golf-tournament-bot")
if BOT_DIR not in sys.path:
    sys.path.insert(0, BOT_DIR)
from src import db  # noqa: E402

GUILD = "test-guild-999"
PARS_18 = ",".join(["4"] * 18)


async def fake_fetch_discord_user(token: str):
    """token-<id> -> a fake Discord user; anything else -> invalid."""
    if token.startswith("token-"):
        uid = token[len("token-"):]
        return {"id": uid, "display_name": f"User{uid}"}
    return None


async def fake_fetch_crew_status(discord_id: str):
    """Default: not crew. Tests override per-case via self._crew(value)."""
    return False


async def fake_fetch_admin_status(discord_id: str):
    """Default: not admin. Tests override per-case via self._admin(value)."""
    return False


async def fake_fetch_mod_admin_status(discord_id: str):
    """Default: not mod/admin. Override per-case via self._mod_admin(value)."""
    return False


async def fake_discord_guild_roles():
    """Default: Discord unreachable. Override per-case."""
    return None


async def fake_discord_guild_members():
    """Default: Discord unreachable. Override per-case."""
    return None


async def fake_discord_modify_member_role(discord_id: str, role_id: str,
                                          action: str):
    """Default: Discord unreachable. Override per-case."""
    return None


def run(coro):
    return asyncio.run(coro)


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        # Fresh DB per test, deleted afterwards: /tmp is a 512MB tmpfs and
        # the suite creates one DB per test, which used to fill it up
        # ("database or disk is full") on full runs.
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        import os

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
        main.discord_guild_roles = fake_discord_guild_roles
        main.discord_guild_members = fake_discord_guild_members
        main.discord_modify_member_role = fake_discord_modify_member_role
        self.client = None
        self._enter_client()

        # Seed: one open tournament, one in-progress, one foreign-guild.
        self.t_open = run(
            db.create_tournament(
                self.db_path, GUILD, "API Open", "stroke", 18,
                "Pebble Beach Golf Links", PARS_18, "open tourney", "1",
            )
        )
        self.t_inprog = run(
            db.create_tournament(
                self.db_path, GUILD, "API Live", "stroke", 18,
                "St Andrews", PARS_18, "live tourney", "1",
            )
        )
        run(db.set_tournament_status(self.db_path, self.t_inprog, "in_progress"))
        self.t_foreign = run(
            db.create_tournament(
                self.db_path, "other-guild", "Foreign", "stroke", 18,
                "Augusta", PARS_18, "foreign", "9",
            )
        )

    def _enter_client(self):
        from fastapi.testclient import TestClient

        self.client = TestClient(app)
        self.client.__enter__()
        self.addCleanup(self._exit_client)

    def _exit_client(self):
        self.client.__exit__(None, None, None)

    # -- helpers ------------------------------------------------------
    def h(self, uid="123"):
        return {"Authorization": f"Bearer token-{uid}"}

    def patch_profile(self, uid, body):
        return self.client.patch("/api/players/me", headers=self.h(uid), json=body)

    def with_tz(self, uid):
        r = self.patch_profile(uid, {"timezone": "America/Denver"})
        self.assertEqual(r.status_code, 200, r.text)
        return r

    # -- auth ---------------------------------------------------------
    def test_health_no_auth(self):
        r = self.client.get("/api/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"ok": True})

    def test_oauth_callback_redirects_to_app(self):
        r = self.client.get("/oauth/callback?code=abc123&state=xyz",
                            follow_redirects=False)
        self.assertEqual(r.status_code, 302)
        loc = r.headers["location"]
        self.assertTrue(loc.startswith("com.alpenglow.vgc.app://oauth-callback?"),
                        loc)
        self.assertIn("code=abc123", loc)
        self.assertIn("state=xyz", loc)

    def test_oauth_callback_error_passthrough(self):
        r = self.client.get("/oauth/callback?error=access_denied",
                            follow_redirects=False)
        self.assertEqual(r.status_code, 302)
        self.assertIn("error=access_denied", r.headers["location"])

    def test_oauth_callback_no_auth_needed(self):
        # No Authorization header — the browser bounce must work anonymous.
        r = self.client.get("/oauth/callback", follow_redirects=False)
        self.assertEqual(r.status_code, 302)

    def test_no_token_401(self):
        r = self.client.get("/api/tournaments")
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json()["detail"], "Invalid Discord token")

    def test_invalid_token_401(self):
        r = self.client.get("/api/tournaments",
                            headers={"Authorization": "Bearer bad-token"})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json()["detail"], "Invalid Discord token")

    def test_valid_token_autocreates_player(self):
        r = self.client.get("/api/players/me", headers=self.h("777"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["discord_id"], "777")
        row = run(db.get_player(self.db_path, "777"))
        self.assertIsNotNone(row)
        self.assertEqual(row["display_name"], "User777")

    # -- tournaments --------------------------------------------------
    def test_list_tournaments(self):
        self.with_tz("123")
        r = self.client.get("/api/tournaments", headers=self.h("123"))
        self.assertEqual(r.status_code, 200)
        rows = r.json()
        self.assertEqual(len(rows), 2)  # only this guild's
        by_name = {t["name"]: t for t in rows}
        self.assertFalse(by_name["API Open"]["registered"])
        self.assertEqual(by_name["API Open"]["tee_position"], "middle")
        self.assertEqual(by_name["API Open"]["green_speed"], "pro")
        self.assertEqual(by_name["API Open"]["holes"], 18)

    def test_register_happy(self):
        self.with_tz("123")
        r = self.client.post(f"/api/tournaments/{self.t_open}/register",
                             headers=self.h("123"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json(), {"registered": True, "already": False})
        self.assertTrue(run(db.is_registered(self.db_path, self.t_open, "123")))

    def test_register_sets_registered_flag(self):
        self.with_tz("123")
        self.client.post(f"/api/tournaments/{self.t_open}/register",
                         headers=self.h("123"))
        r = self.client.get("/api/tournaments", headers=self.h("123"))
        by_name = {t["name"]: t for t in r.json()}
        self.assertTrue(by_name["API Open"]["registered"])

    def test_register_already(self):
        self.with_tz("123")
        self.client.post(f"/api/tournaments/{self.t_open}/register",
                         headers=self.h("123"))
        r = self.client.post(f"/api/tournaments/{self.t_open}/register",
                             headers=self.h("123"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"registered": True, "already": True})

    def test_register_404_foreign_guild(self):
        self.with_tz("123")
        r = self.client.post(f"/api/tournaments/{self.t_foreign}/register",
                             headers=self.h("123"))
        self.assertEqual(r.status_code, 404)

    def test_register_404_missing(self):
        self.with_tz("123")
        r = self.client.post("/api/tournaments/999999/register",
                             headers=self.h("123"))
        self.assertEqual(r.status_code, 404)

    def test_register_409_closed(self):
        self.with_tz("123")
        r = self.client.post(f"/api/tournaments/{self.t_inprog}/register",
                             headers=self.h("123"))
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["code"], "registration_closed")

    def test_register_409_timezone_required(self):
        # Fresh user 888 never set a timezone.
        r = self.client.post(f"/api/tournaments/{self.t_open}/register",
                             headers=self.h("888"))
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["code"], "timezone_required")

    def test_unregister(self):
        self.with_tz("123")
        self.client.post(f"/api/tournaments/{self.t_open}/register",
                         headers=self.h("123"))
        r = self.client.delete(f"/api/tournaments/{self.t_open}/register",
                               headers=self.h("123"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"registered": False})
        self.assertFalse(run(db.is_registered(self.db_path, self.t_open, "123")))

    def test_unregister_idempotent(self):
        self.with_tz("123")
        r = self.client.delete(f"/api/tournaments/{self.t_open}/register",
                               headers=self.h("123"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"registered": False})

    # -- tee times ----------------------------------------------------
    def _create(self, uid, **kw):
        body = {"label": "Morning flight", "date": "2026-10-03",
                "time": "09:30", "max_players": 4}
        body.update(kw)
        return self.client.post(
            f"/api/tournaments/{self.t_open}/tee-times",
            headers=self.h(uid), json=body)

    def test_create_tee_time_happy(self):
        self.with_tz("123")
        r = self._create("123")
        self.assertEqual(r.status_code, 200, r.text)
        tt = r.json()
        self.assertEqual(tt["label"], "Morning flight")
        # 09:30 America/Denver == 15:30 UTC (Oct: MDT).
        self.assertEqual(tt["starts_at"], "2026-10-03T15:30:00+00:00")
        self.assertEqual(tt["max_players"], 4)
        self.assertEqual(tt["created_by"], "123")
        self.assertTrue(tt["joined"])
        players = [p["discord_id"] for p in tt["players"]]
        self.assertEqual(players, ["123"])  # creator auto-joined

    def test_create_tee_time_409_timezone_required(self):
        r = self._create("888")  # no timezone set
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["code"], "timezone_required")

    def test_create_tee_time_bad_date(self):
        self.with_tz("123")
        for bad in ["2026-13-01", "2026-02-30", "not-a-date", "10/03/2026"]:
            r = self._create("123", date=bad)
            self.assertEqual(r.status_code, 422, bad)

    def test_create_tee_time_bad_time(self):
        self.with_tz("123")
        for bad in ["25:00", "9:30", "19:60", "7pm", ""]:
            r = self._create("123", time=bad)
            self.assertEqual(r.status_code, 422, bad)

    def test_create_tee_time_max_players_range(self):
        self.with_tz("123")
        for bad in [0, 5, 8, -1]:
            r = self._create("123", max_players=bad)
            self.assertEqual(r.status_code, 422, str(bad))

    def test_create_tee_time_empty_label(self):
        self.with_tz("123")
        for bad in ["", "   "]:
            r = self._create("123", label=bad)
            self.assertEqual(r.status_code, 422, repr(bad))

    def test_create_tee_time_404_tournament(self):
        self.with_tz("123")
        r = self.client.post("/api/tournaments/999999/tee-times",
                             headers=self.h("123"),
                             json={"label": "x", "date": "2026-10-03",
                                   "time": "09:30", "max_players": 4})
        self.assertEqual(r.status_code, 404)

    def test_list_tee_times(self):
        self.with_tz("123")
        self._create("123")
        r = self.client.get(f"/api/tournaments/{self.t_open}/tee-times",
                            headers=self.h("123"))
        self.assertEqual(r.status_code, 200)
        rows = r.json()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["players"][0]["display_name"], "User123")
        self.assertIn("golfplus_handle", rows[0]["players"][0])

    # -- tee time edit/delete ------------------------------------------
    def _tt_id(self, uid="123"):
        self.with_tz(uid)
        r = self._create(uid)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["id"]

    def test_patch_tee_time_label_by_creator(self):
        tt_id = self._tt_id("123")
        r = self.client.patch(f"/api/tee-times/{tt_id}",
                              headers=self.h("123"),
                              json={"label": "Evening flight"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["label"], "Evening flight")

    def test_patch_tee_time_time_keeps_date(self):
        tt_id = self._tt_id("123")  # 09:30 America/Denver
        r = self.client.patch(f"/api/tee-times/{tt_id}",
                              headers=self.h("123"),
                              json={"time": "18:00"})
        self.assertEqual(r.status_code, 200, r.text)
        # Date kept, time moved: 18:00 MDT == 2026-10-04T00:00:00Z.
        self.assertEqual(r.json()["starts_at"], "2026-10-04T00:00:00+00:00")

    def test_patch_tee_time_date_keeps_time(self):
        tt_id = self._tt_id("123")
        r = self.client.patch(f"/api/tee-times/{tt_id}",
                              headers=self.h("123"),
                              json={"date": "2026-10-05"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["starts_at"], "2026-10-05T15:30:00+00:00")

    def test_patch_tee_time_403_not_creator_or_crew(self):
        tt_id = self._tt_id("123")
        self.with_tz("456")
        r = self.client.patch(f"/api/tee-times/{tt_id}",
                              headers=self.h("456"),
                              json={"label": "hijack"})
        self.assertEqual(r.status_code, 403)

    def test_patch_tee_time_crew_can_edit(self):
        tt_id = self._tt_id("123")
        self.with_tz("456")

        async def fake_crew(discord_id):
            return True

        main.fetch_crew_status = fake_crew
        try:
            r = self.client.patch(f"/api/tee-times/{tt_id}",
                                  headers=self.h("456"),
                                  json={"label": "crew fix"})
        finally:
            main.fetch_crew_status = fake_fetch_crew_status
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["label"], "crew fix")

    def test_patch_tee_time_422_empty_body(self):
        tt_id = self._tt_id("123")
        r = self.client.patch(f"/api/tee-times/{tt_id}",
                              headers=self.h("123"), json={})
        self.assertEqual(r.status_code, 422)

    def test_patch_tee_time_404(self):
        self.with_tz("123")
        r = self.client.patch("/api/tee-times/999999",
                              headers=self.h("123"),
                              json={"label": "x"})
        self.assertEqual(r.status_code, 404)

    def test_delete_tee_time_by_creator(self):
        tt_id = self._tt_id("123")
        r = self.client.delete(f"/api/tee-times/{tt_id}",
                               headers=self.h("123"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json(), {"deleted": True})
        r = self.client.get(f"/api/tournaments/{self.t_open}/tee-times",
                            headers=self.h("123"))
        self.assertEqual(r.json(), [])

    def test_delete_tee_time_403_not_creator(self):
        tt_id = self._tt_id("123")
        self.with_tz("456")
        r = self.client.delete(f"/api/tee-times/{tt_id}",
                               headers=self.h("456"))
        self.assertEqual(r.status_code, 403)

    def test_delete_tee_time_409_when_scores_exist(self):
        tt_id = self._tt_id("123")
        run(db.upsert_scorecard(self.db_path, self.t_open, "123", None,
                                tt_id, [4] * 18, "verified",
                                submitted_by="123"))
        r = self.client.delete(f"/api/tee-times/{tt_id}",
                               headers=self.h("123"))
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["code"], "tee_time_has_scores")

    def test_join_happy(self):
        self.with_tz("123")
        self.with_tz("456")
        tt_id = self._create("123").json()["id"]
        r = self.client.post(f"/api/tee-times/{tt_id}/join", headers=self.h("456"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"joined": True, "already": False})

    def test_join_already(self):
        self.with_tz("123")
        tt_id = self._create("123").json()["id"]
        r = self.client.post(f"/api/tee-times/{tt_id}/join", headers=self.h("123"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"joined": True, "already": True})

    def test_join_full(self):
        self.with_tz("123")
        self.with_tz("456")
        tt_id = self._create("123", max_players=1).json()["id"]
        r = self.client.post(f"/api/tee-times/{tt_id}/join", headers=self.h("456"))
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["code"], "tee_time_full")

    def test_join_missing(self):
        r = self.client.post("/api/tee-times/999999/join", headers=self.h("123"))
        self.assertEqual(r.status_code, 404)

    def test_leave(self):
        self.with_tz("123")
        self.with_tz("456")
        tt_id = self._create("123").json()["id"]
        self.client.post(f"/api/tee-times/{tt_id}/join", headers=self.h("456"))
        r = self.client.post(f"/api/tee-times/{tt_id}/leave", headers=self.h("456"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"left": True})
        players = run(db.get_tee_time_players(self.db_path, tt_id))
        self.assertEqual([p["discord_id"] for p in players], ["123"])

    # -- join requests ------------------------------------------------
    def test_request_happy_and_pending(self):
        self.with_tz("123")
        self.with_tz("456")
        tt_id = self._create("123").json()["id"]
        r = self.client.post(f"/api/tee-times/{tt_id}/request",
                             headers=self.h("456"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json(), {"requested": True, "already": False})
        r = self.client.post(f"/api/tee-times/{tt_id}/request",
                             headers=self.h("456"))
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["code"], "request_pending")

    def test_request_already_in_tee_time(self):
        self.with_tz("123")
        tt_id = self._create("123").json()["id"]
        r = self.client.post(f"/api/tee-times/{tt_id}/request",
                             headers=self.h("123"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"requested": True, "already": True})

    def test_request_full(self):
        self.with_tz("123")
        self.with_tz("456")
        tt_id = self._create("123", max_players=1).json()["id"]
        r = self.client.post(f"/api/tee-times/{tt_id}/request",
                             headers=self.h("456"))
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["code"], "tee_time_full")

    def _pending_request_id(self, tt_id, requester="456"):
        rows = run(db.list_pending_join_requests(self.db_path, tt_id))
        self.assertEqual(len(rows), 1)
        return rows[0]["id"]

    def test_requests_creator_only_list(self):
        self.with_tz("123")
        self.with_tz("456")
        tt_id = self._create("123").json()["id"]
        self.client.post(f"/api/tee-times/{tt_id}/request", headers=self.h("456"))
        r = self.client.get(f"/api/tee-times/{tt_id}/requests",
                            headers=self.h("456"))
        self.assertEqual(r.status_code, 403)
        r = self.client.get(f"/api/tee-times/{tt_id}/requests",
                            headers=self.h("123"))
        self.assertEqual(r.status_code, 200)
        rows = r.json()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["player_discord_id"], "456")
        self.assertEqual(rows[0]["status"], "pending")

    def test_approve_adds_player(self):
        self.with_tz("123")
        self.with_tz("456")
        self.client.post(f"/api/tournaments/{self.t_open}/register",
                         headers=self.h("456"))
        tt_id = self._create("123").json()["id"]
        self.client.post(f"/api/tee-times/{tt_id}/request", headers=self.h("456"))
        req_id = self._pending_request_id(tt_id)
        r = self.client.post(
            f"/api/tee-times/{tt_id}/requests/{req_id}/approve",
            headers=self.h("123"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["status"], "accepted")
        self.assertEqual(r.json()["decided_by"], "123")
        players = run(db.get_tee_time_players(self.db_path, tt_id))
        self.assertIn("456", [p["discord_id"] for p in players])

    def test_approve_non_creator_403(self):
        self.with_tz("123")
        self.with_tz("456")
        tt_id = self._create("123").json()["id"]
        self.client.post(f"/api/tee-times/{tt_id}/request", headers=self.h("456"))
        req_id = self._pending_request_id(tt_id)
        r = self.client.post(
            f"/api/tee-times/{tt_id}/requests/{req_id}/approve",
            headers=self.h("456"))
        self.assertEqual(r.status_code, 403)

    def test_approve_twice_409(self):
        self.with_tz("123")
        self.with_tz("456")
        tt_id = self._create("123").json()["id"]
        self.client.post(f"/api/tee-times/{tt_id}/request", headers=self.h("456"))
        req_id = self._pending_request_id(tt_id)
        self.client.post(f"/api/tee-times/{tt_id}/requests/{req_id}/approve",
                         headers=self.h("123"))
        r = self.client.post(f"/api/tee-times/{tt_id}/requests/{req_id}/approve",
                             headers=self.h("123"))
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["code"], "request_already_decided")

    def test_decline(self):
        self.with_tz("123")
        self.with_tz("456")
        tt_id = self._create("123").json()["id"]
        self.client.post(f"/api/tee-times/{tt_id}/request", headers=self.h("456"))
        req_id = self._pending_request_id(tt_id)
        r = self.client.post(
            f"/api/tee-times/{tt_id}/requests/{req_id}/decline",
            headers=self.h("123"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "declined")
        players = run(db.get_tee_time_players(self.db_path, tt_id))
        self.assertNotIn("456", [p["discord_id"] for p in players])

    # -- scorecards ---------------------------------------------------
    def _past_tee_time(self, tournament, creator="123", max_players=4,
                       extra_players=()):
        """Insert a tee time directly (past start) — the API has no reason to
        create past tee times."""
        tt_id = run(db.create_tee_time(
            self.db_path, tournament, "past flight",
            "2026-01-02T15:30:00+00:00", max_players, creator, None))
        run(db.join_tee_time(self.db_path, tt_id, creator))
        for pid in extra_players:
            run(db.join_tee_time(self.db_path, tt_id, pid))
        # Score entry requires Start Round to have been pressed.
        run(db.start_tournament_tee_time(self.db_path, tt_id, creator))
        return tt_id

    def _start_tee_time(self, tt_id, uid="123"):
        """Press Start Round via the API (any player in the tee time)."""
        r = self.client.post(f"/api/tee-times/{tt_id}/start",
                             headers=self.h(uid))
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_scorecard_get_none(self):
        self.with_tz("123")
        tt_id = self._past_tee_time(self.t_open)
        r = self.client.get(f"/api/tee-times/{tt_id}/scorecard",
                            headers=self.h("123"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"card": None})

    def test_scorecard_early_409(self):
        self.with_tz("123")
        tt_id = self._create("123", date="2026-12-25", time="09:30").json()["id"]
        body = {"player_discord_id": "123", "scores": [4] * 18}
        r = self.client.put(f"/api/tee-times/{tt_id}/scorecard",
                            headers=self.h("123"), json=body)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["code"], "tee_time_not_passed")

    def test_scorecard_incomplete_422(self):
        self.with_tz("123")
        tt_id = self._past_tee_time(self.t_open)
        body = {"player_discord_id": "123", "scores": [4] * 17}
        r = self.client.put(f"/api/tee-times/{tt_id}/scorecard",
                            headers=self.h("123"), json=body)
        self.assertEqual(r.status_code, 422)

    def test_scorecard_bad_values_422(self):
        self.with_tz("123")
        tt_id = self._past_tee_time(self.t_open)
        for bad in ([4] * 17 + [0], [4] * 17 + [16], [4] * 17 + ["x"]):
            body = {"player_discord_id": "123", "scores": bad}
            r = self.client.put(f"/api/tee-times/{tt_id}/scorecard",
                                headers=self.h("123"), json=body)
            self.assertEqual(r.status_code, 422, str(bad))

    def test_scorecard_caller_not_in_tee_time_403(self):
        self.with_tz("123")
        self.with_tz("456")
        tt_id = self._past_tee_time(self.t_open, creator="123")
        body = {"player_discord_id": "123", "scores": [4] * 18}
        r = self.client.put(f"/api/tee-times/{tt_id}/scorecard",
                            headers=self.h("456"), json=body)
        self.assertEqual(r.status_code, 403)

    def test_scorecard_owner_not_in_tee_time_403(self):
        self.with_tz("123")
        self.with_tz("456")
        self.with_tz("789")
        tt_id = self._past_tee_time(self.t_open, creator="123", extra_players=["456"])
        body = {"player_discord_id": "789", "scores": [4] * 18}
        r = self.client.put(f"/api/tee-times/{tt_id}/scorecard",
                            headers=self.h("123"), json=body)
        self.assertEqual(r.status_code, 403)

    def test_scorecard_submit_happy_and_get(self):
        self.with_tz("123")
        self.with_tz("456")
        tt_id = self._past_tee_time(self.t_open, creator="123", extra_players=["456"])
        scores = [4] * 18
        body = {"player_discord_id": "123", "scores": scores, "complete": True}
        r = self.client.put(f"/api/tee-times/{tt_id}/scorecard",
                            headers=self.h("123"), json=body)
        self.assertEqual(r.status_code, 200, r.text)
        card = r.json()["card"]
        self.assertEqual(card["scores"], scores)
        self.assertEqual(card["total"], 72)
        self.assertEqual(card["to_par"], 0)
        self.assertEqual(card["status"], "verified")  # 2 players -> partners
        self.assertEqual(card["submitted_by"], "123")

        r = self.client.get(f"/api/tee-times/{tt_id}/scorecard",
                            headers=self.h("123"))
        self.assertEqual(r.json()["card"]["total"], 72)

    def test_scorecard_submit_enqueues_leaderboard_refresh(self):
        # A submitted card enqueues a scorecard_submitted event so the bot's
        # outbox drain re-renders the Discord leaderboard board.
        self.with_tz("123")
        self.with_tz("456")
        run(db.poll_outbox(self.db_path))  # clear setup noise
        tt_id = self._past_tee_time(self.t_open, creator="123", extra_players=["456"])
        run(db.ack_outbox(self.db_path,
                          [r["id"] for r in run(db.poll_outbox(self.db_path))]))
        body = {"player_discord_id": "123", "scores": [4] * 18,
                "complete": True}
        r = self.client.put(f"/api/tee-times/{tt_id}/scorecard",
                            headers=self.h("123"), json=body)
        self.assertEqual(r.status_code, 200, r.text)
        rows = run(db.poll_outbox(self.db_path))
        kinds = [(row["kind"], row["payload"].get("tournament_id"))
                 for row in rows]
        self.assertIn(("scorecard_submitted", self.t_open), kinds)

    def test_scorecard_solo_pending(self):
        self.with_tz("123")
        tt_id = self._past_tee_time(self.t_open, creator="123")
        body = {"player_discord_id": "123", "scores": [4] * 18,
                "complete": True}
        r = self.client.put(f"/api/tee-times/{tt_id}/scorecard",
                            headers=self.h("123"), json=body)
        self.assertEqual(r.json()["card"]["status"], "pending")

    def test_scorecard_other_player_in_tee_time(self):
        # The bot lets a playing partner enter someone else's card.
        self.with_tz("123")
        self.with_tz("456")
        tt_id = self._past_tee_time(self.t_open, creator="123", extra_players=["456"])
        body = {"player_discord_id": "456", "scores": [5] * 18,
                "complete": True}
        r = self.client.put(f"/api/tee-times/{tt_id}/scorecard",
                            headers=self.h("123"), json=body)
        self.assertEqual(r.status_code, 200, r.text)
        card = r.json()["card"]
        self.assertEqual(card["player_discord_id"], "456")
        self.assertEqual(card["total"], 90)
        self.assertEqual(card["submitted_by"], "123")

    def test_scorecard_resubmit_locked_for_non_crew(self):
        # First submission by a member is fine; a second PUT by a non-crew
        # member is rejected with scorecard_locked.
        self.with_tz("123")
        self.with_tz("456")
        tt_id = self._past_tee_time(self.t_open, creator="123", extra_players=["456"])
        body = {"player_discord_id": "123", "scores": [4] * 18,
                "complete": True}
        r = self.client.put(f"/api/tee-times/{tt_id}/scorecard",
                            headers=self.h("123"), json=body)
        self.assertEqual(r.status_code, 200, r.text)
        # Same player tries to change their own submitted card.
        body2 = {"player_discord_id": "123", "scores": [3] * 18}
        r = self.client.put(f"/api/tee-times/{tt_id}/scorecard",
                            headers=self.h("123"), json=body2)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["code"], "scorecard_locked")
        # A partner also can't overwrite someone else's submitted card.
        r = self.client.put(f"/api/tee-times/{tt_id}/scorecard",
                            headers=self.h("456"), json=body2)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["code"], "scorecard_locked")

    def test_scorecard_resubmit_crew_can_edit(self):
        self.with_tz("123")
        tt_id = self._past_tee_time(self.t_open, creator="123")
        body = {"player_discord_id": "123", "scores": [4] * 18,
                "complete": True}
        r = self.client.put(f"/api/tee-times/{tt_id}/scorecard",
                            headers=self.h("123"), json=body)
        self.assertEqual(r.status_code, 200, r.text)

        async def fake_crew(discord_id):
            return True
        main.fetch_crew_status = fake_crew
        try:
            body2 = {"player_discord_id": "123", "scores": [3] * 18}
            r = self.client.put(f"/api/tee-times/{tt_id}/scorecard",
                                headers=self.h("123"), json=body2)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["card"]["total"], 54)
        finally:
            main.fetch_crew_status = fake_fetch_crew_status


    def test_scorecard_live_partial_save_and_finalize(self):
        # Live scoring: partial PUTs save an in_progress card hole by hole;
        # complete=True finalizes it; the old 403 lock only applies after.
        self.with_tz("123")
        self.with_tz("456")
        tt_id = self._past_tee_time(self.t_open, creator="123", extra_players=["456"])
        url = f"/api/tee-times/{tt_id}/scorecard"
        # First holes go in: nulls allowed, card is in_progress.
        scores = [4, 5, 3] + [None] * 15
        r = self.client.put(url, headers=self.h("123"),
                            json={"player_discord_id": "123",
                                  "scores": scores})
        self.assertEqual(r.status_code, 200, r.text)
        card = r.json()["card"]
        self.assertEqual(card["status"], "in_progress")
        self.assertEqual(card["thru"], 3)
        self.assertEqual(card["total"], 12)
        # More holes merge into the same row; earlier holes are kept.
        scores2 = [None, None, None, 4, 4] + [None] * 13
        r = self.client.put(url, headers=self.h("123"),
                            json={"player_discord_id": "123",
                                  "scores": scores2})
        self.assertEqual(r.status_code, 200, r.text)
        card = r.json()["card"]
        self.assertEqual(card["thru"], 5)
        self.assertEqual(card["scores"][:5], [4, 5, 3, 4, 4])
        # A second player can still PUT while the card is in progress.
        r = self.client.put(url, headers=self.h("123"),
                            json={"player_discord_id": "123",
                                  "scores": scores2})
        self.assertEqual(r.status_code, 200, r.text)
        # Finalize with the full card.
        full = [4, 5, 3, 4, 4] + [4] * 13
        r = self.client.put(url, headers=self.h("123"),
                            json={"player_discord_id": "123",
                                  "scores": full, "complete": True})
        self.assertEqual(r.status_code, 200, r.text)
        card = r.json()["card"]
        self.assertEqual(card["status"], "verified")  # 2 players -> partners
        self.assertEqual(card["total"], sum(full))
        # Now the lock applies: non-crew can't touch it.
        r = self.client.put(url, headers=self.h("123"),
                            json={"player_discord_id": "123",
                                  "scores": [3] * 18, "complete": True})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["code"], "scorecard_locked")

    def test_scorecard_complete_requires_all_holes(self):
        # complete=True with holes still empty is rejected.
        self.with_tz("123")
        tt_id = self._past_tee_time(self.t_open, creator="123")
        url = f"/api/tee-times/{tt_id}/scorecard"
        scores = [4] * 6 + [None] * 12
        r = self.client.put(url, headers=self.h("123"),
                            json={"player_discord_id": "123",
                                  "scores": scores, "complete": True})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["code"], "scorecard_incomplete")

    def test_scorecard_live_shows_on_leaderboard(self):
        # An in-progress card appears on the live leaderboard with thru.
        self.with_tz("123")
        self.with_tz("456")
        tt_id = self._past_tee_time(self.t_open, creator="123", extra_players=["456"])
        scores = [4, 5] + [None] * 16
        r = self.client.put(f"/api/tee-times/{tt_id}/scorecard",
                            headers=self.h("123"),
                            json={"player_discord_id": "123",
                                  "scores": scores})
        self.assertEqual(r.status_code, 200, r.text)
        r = self.client.get(f"/api/tournaments/{self.t_open}/leaderboard",
                            headers=self.h("123"))
        self.assertEqual(r.status_code, 200, r.text)
        standings = r.json()["standings"]
        self.assertEqual(len(standings), 1)
        row = standings[0]
        self.assertTrue(row["on_course"])
        self.assertEqual(row["thru"], 2)
        self.assertEqual(row["total"], 9)

    def test_scorecard_get_other_player_card(self):
        # The player picker loads another tee-time member's card for the
        # read-only submitted view.
        self.with_tz("123")
        self.with_tz("456")
        tt_id = self._past_tee_time(self.t_open, creator="123", extra_players=["456"])
        run(db.upsert_scorecard(self.db_path, self.t_open, "123", None, tt_id,
                                [4] * 18, "verified", submitted_by="123"))
        r = self.client.get(
            f"/api/tee-times/{tt_id}/scorecard?player_discord_id=123",
            headers=self.h("456"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["card"]["total"], 72)
        # Someone outside the tee time gets a 403.
        r = self.client.get(
            f"/api/tee-times/{tt_id}/scorecard?player_discord_id=999",
            headers=self.h("456"))
        self.assertEqual(r.status_code, 403)

    # -- leaderboard --------------------------------------------------
    def test_leaderboard_stroke(self):
        self.with_tz("123")
        self.with_tz("456")
        tt_id = self._past_tee_time(self.t_open, creator="123", extra_players=["456"])
        run(db.upsert_scorecard(self.db_path, self.t_open, "123", None, tt_id,
                                [4] * 18, "verified", submitted_by="123"))
        run(db.upsert_scorecard(self.db_path, self.t_open, "456", None, tt_id,
                                [5] * 18, "verified", submitted_by="456"))
        r = self.client.get(f"/api/tournaments/{self.t_open}/leaderboard",
                            headers=self.h("123"))
        self.assertEqual(r.status_code, 200, r.text)
        lb = r.json()
        self.assertEqual(lb["format"], "stroke")
        self.assertIn("Middle tees", lb["settings"])
        rows = lb["standings"]
        self.assertEqual([x["discord_id"] for x in rows], ["123", "456"])
        self.assertEqual(rows[0]["total"], 72)
        self.assertEqual(rows[0]["to_par"], 0)
        self.assertEqual(rows[0]["to_par_display"], "E")
        self.assertEqual(rows[1]["total"], 90)
        self.assertEqual(rows[1]["to_par_display"], "+18")

    def test_leaderboard_404(self):
        r = self.client.get("/api/tournaments/999999/leaderboard",
                            headers=self.h("123"))
        self.assertEqual(r.status_code, 404)

    def test_leaderboard_match(self):
        t = run(db.create_tournament(
            self.db_path, GUILD, "Match Night", "match", 9,
            "Pebble Beach Golf Links", PARS_18[:35], "m", "1"))
        mid = run(db.create_match(self.db_path, t, "123", "456", "123",
                                  winner="player1"))
        run(db.confirm_match(self.db_path, mid, "player1"))
        r = self.client.get(f"/api/tournaments/{t}/leaderboard",
                            headers=self.h("123"))
        self.assertEqual(r.status_code, 200, r.text)
        rows = r.json()["standings"]
        self.assertEqual(rows[0]["discord_id"], "123")
        self.assertEqual((rows[0]["wins"], rows[0]["losses"], rows[0]["ties"]),
                         (1, 0, 0))

    # -- seasons ------------------------------------------------------
    def test_seasons_none_404(self):
        r = self.client.get("/api/seasons/standings", headers=self.h("123"))
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["code"], "no_active_season")

    def test_seasons_standings(self):
        sid = run(db.create_season(self.db_path, GUILD, "Fall 2026", "123"))
        run(db.add_tournament_to_season(self.db_path, sid, self.t_open))
        run(db.record_season_points(self.db_path, sid, self.t_open,
                                    [("123", 1, 100), ("456", 2, 90)]))
        r = self.client.get("/api/seasons/standings", headers=self.h("123"))
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["season"]["name"], "Fall 2026")
        rows = body["standings"]
        self.assertEqual(rows[0]["discord_id"], "123")
        self.assertEqual(rows[0]["total_points"], 100)
        self.assertEqual(rows[1]["discord_id"], "456")

    def test_create_tournament_auto_links_active_season(self):
        sid = run(db.create_season(self.db_path, GUILD, "Fall 2026", "123"))
        self._crew(True)
        r = self.client.post("/api/tournaments", headers=self.h("123"),
                             json=self._create_body())
        self.assertEqual(r.status_code, 201, r.text)
        tid = r.json()["id"]
        seasons = run(db.get_seasons_for_tournament(self.db_path, tid,
                                                    "active"))
        self.assertEqual([s["id"] for s in seasons], [sid])

    def test_create_tournament_no_active_season_no_link(self):
        self._crew(True)
        r = self.client.post("/api/tournaments", headers=self.h("123"),
                             json=self._create_body())
        self.assertEqual(r.status_code, 201, r.text)
        tid = r.json()["id"]
        seasons = run(db.get_seasons_for_tournament(self.db_path, tid,
                                                    "active"))
        self.assertEqual(seasons, [])

    # -- season create / complete -------------------------------------
    def _season_body(self, name="Winter 2026", start="2026-10-01",
                     end="2027-03-31"):
        body = {"name": name}
        if start is not None:
            body["start_date"] = start
        if end is not None:
            body["end_date"] = end
        return body

    def test_create_season_success(self):
        self._admin(True)
        r = self.client.post("/api/seasons", headers=self.h("1"),
                             json=self._season_body())
        self.assertEqual(r.status_code, 201, r.text)
        body = r.json()
        self.assertEqual(body["name"], "Winter 2026")
        self.assertEqual(body["status"], "active")
        self.assertEqual(body["start_date"], "2026-10-01")
        self.assertEqual(body["end_date"], "2027-03-31")
        season = run(db.get_season(self.db_path, body["id"]))
        self.assertEqual(season["name"], "Winter 2026")

    def test_create_season_no_dates(self):
        self._admin(True)
        r = self.client.post("/api/seasons", headers=self.h("1"),
                             json={"name": "Open Season"})
        self.assertEqual(r.status_code, 201, r.text)
        body = r.json()
        self.assertIsNone(body["start_date"])
        self.assertIsNone(body["end_date"])

    def test_create_season_409_when_active_exists(self):
        self._admin(True)
        run(db.create_season(self.db_path, GUILD, "Fall 2026", "1"))
        r = self.client.post("/api/seasons", headers=self.h("1"),
                             json=self._season_body())
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(r.json()["code"], "active_season_exists")

    def test_create_season_422_blank_name(self):
        self._admin(True)
        r = self.client.post("/api/seasons", headers=self.h("1"),
                             json=self._season_body(name="   "))
        self.assertEqual(r.status_code, 422, r.text)

    def test_create_season_422_start_after_end(self):
        self._admin(True)
        r = self.client.post("/api/seasons", headers=self.h("1"),
                             json=self._season_body(start="2027-04-01",
                                                    end="2027-03-31"))
        self.assertEqual(r.status_code, 422, r.text)

    def test_create_season_422_bad_date(self):
        self._admin(True)
        r = self.client.post("/api/seasons", headers=self.h("1"),
                             json=self._season_body(start="not-a-date"))
        self.assertEqual(r.status_code, 422, r.text)

    def test_create_season_403_not_admin(self):
        self._admin(False)
        r = self.client.post("/api/seasons", headers=self.h("1"),
                             json=self._season_body())
        self.assertEqual(r.status_code, 403, r.text)

    def test_complete_season_success(self):
        self._admin(True)
        sid = run(db.create_season(self.db_path, GUILD, "Fall 2026", "1"))
        r = self.client.post(f"/api/seasons/{sid}/complete",
                             headers=self.h("1"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["status"], "completed")
        season = run(db.get_season(self.db_path, sid))
        self.assertEqual(season["status"], "completed")

    def test_complete_season_404_missing(self):
        self._admin(True)
        r = self.client.post("/api/seasons/99999/complete",
                             headers=self.h("1"))
        self.assertEqual(r.status_code, 404, r.text)
        self.assertEqual(r.json()["code"], "season_not_found")

    def test_complete_season_409_already_completed(self):
        self._admin(True)
        sid = run(db.create_season(self.db_path, GUILD, "Fall 2026", "1"))
        run(db.complete_season(self.db_path, sid))
        r = self.client.post(f"/api/seasons/{sid}/complete",
                             headers=self.h("1"))
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(r.json()["code"], "season_not_active")

    def test_complete_season_403_not_admin(self):
        self._admin(False)
        sid = run(db.create_season(self.db_path, GUILD, "Fall 2026", "1"))
        r = self.client.post(f"/api/seasons/{sid}/complete",
                             headers=self.h("1"))
        self.assertEqual(r.status_code, 403, r.text)

    def test_seasons_standings_includes_dates(self):
        sid = run(db.create_season(self.db_path, GUILD, "Fall 2026", "123",
                                   start_date="2026-09-01",
                                   end_date="2026-12-31"))
        r = self.client.get("/api/seasons/standings", headers=self.h("123"))
        self.assertEqual(r.status_code, 200, r.text)
        season = r.json()["season"]
        self.assertEqual(season["id"], sid)
        self.assertEqual(season["start_date"], "2026-09-01")
        self.assertEqual(season["end_date"], "2026-12-31")

    # -- profile ------------------------------------------------------
    def test_profile_fields(self):
        self.with_tz("123")
        self.patch_profile("123", {"golfplus_handle": "  Alumec  "})
        r = self.client.get("/api/players/me", headers=self.h("123"))
        self.assertEqual(r.status_code, 200)
        me = r.json()
        self.assertEqual(me["discord_id"], "123")
        self.assertEqual(me["display_name"], "User123")
        self.assertEqual(me["golfplus_handle"], "Alumec")
        self.assertEqual(me["timezone"], "America/Denver")

    def test_patch_bad_timezone_422(self):
        r = self.patch_profile("123", {"timezone": "Mars/Olympus"})
        self.assertEqual(r.status_code, 422)

    def test_patch_empty_handle_unlinks(self):
        self.patch_profile("123", {"golfplus_handle": "Alumec"})
        r = self.patch_profile("123", {"golfplus_handle": "   "})
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.json()["golfplus_handle"])

    # -- stats --------------------------------------------------------
    def test_stats_with_rounds_and_match(self):
        self.with_tz("123")
        tt_id = self._past_tee_time(self.t_open, creator="123", extra_players=["456"])
        # 18 holes: 17 pars (4) + one birdie (3) -> total 71.
        scores = [3] + [4] * 17
        run(db.upsert_scorecard(self.db_path, self.t_open, "123", None, tt_id,
                                scores, "verified", submitted_by="123"))
        mid = run(db.create_match(self.db_path, self.t_open, "123", "456",
                                  "123", winner="player1"))
        run(db.confirm_match(self.db_path, mid, "player1"))
        r = self.client.get("/api/players/me/stats", headers=self.h("123"))
        self.assertEqual(r.status_code, 200, r.text)
        s = r.json()
        self.assertEqual(s["rounds_played"], 1)
        self.assertEqual(s["best_total"], 71)
        self.assertEqual(s["best_to_par"], -1)
        self.assertEqual(s["best_to_par_display"], "-1")
        self.assertEqual(s["birdies_or_better"], 1)
        self.assertEqual(s["pars_made"], 17)
        self.assertEqual(s["best_par_streak"], 18)
        self.assertEqual(s["match_record"], {"wins": 1, "losses": 0, "ties": 0})
        self.assertEqual(s["confirmed_matches"], 1)

    def test_stats_no_rounds(self):
        r = self.client.get("/api/players/me/stats", headers=self.h("321"))
        self.assertEqual(r.status_code, 200)
        s = r.json()
        self.assertEqual(s["rounds_played"], 0)
        self.assertIsNone(s["best_total"])
        self.assertEqual(s["match_record"], {"wins": 0, "losses": 0, "ties": 0})

    # -- courses ------------------------------------------------------
    def test_courses_no_auth_401(self):
        r = self.client.get("/api/courses")
        self.assertEqual(r.status_code, 401)

    def test_list_courses(self):
        r = self.client.get("/api/courses", headers=self.h("123"))
        self.assertEqual(r.status_code, 200, r.text)
        courses = r.json()
        self.assertEqual(len(courses), 18)
        pebble = next(
            c for c in courses if c["name"] == "Pebble Beach Golf Links")
        self.assertEqual(len(pebble["pars"]), 18)
        self.assertEqual(pebble["par_total"], sum(pebble["pars"]))
        self.assertEqual(pebble["par_total"], 72)

    # -- create tournament (crew-gated) --------------------------------
    def _crew(self, value):
        async def fake(discord_id):
            return value
        main.fetch_crew_status = fake

    def _create_body(self, **over):
        body = {
            "name": "App Open",
            "format": "stroke",
            "holes": 18,
            "course": "Pebble Beach Golf Links",
            "start_date": "2026-10-03",
            "end_date": "2026-10-10",
        }
        body.update(over)
        return body

    def test_create_tournament_403_not_crew(self):
        self._crew(False)
        r = self.client.post("/api/tournaments", headers=self.h("123"),
                             json=self._create_body())
        self.assertEqual(r.status_code, 403, r.text)

    def test_create_tournament_503_unverifiable(self):
        self._crew(None)
        r = self.client.post("/api/tournaments", headers=self.h("123"),
                             json=self._create_body())
        self.assertEqual(r.status_code, 503, r.text)

    def test_create_tournament_happy_autofills_pars(self):
        self._crew(True)
        r = self.client.post(
            "/api/tournaments", headers=self.h("123"),
            json=self._create_body(
                tee_position="back", pin_position="black",
                wind_strength="severe", green_speed="veryfast",
                description="created from the app"))
        self.assertEqual(r.status_code, 201, r.text)
        t = r.json()
        self.assertEqual(t["name"], "App Open")
        self.assertEqual(t["status"], "registration_open")
        self.assertEqual(t["tee_position"], "back")
        self.assertEqual(t["pin_position"], "black")
        self.assertEqual(t["wind_strength"], "severe")
        self.assertEqual(t["green_speed"], "veryfast")
        # Pars auto-filled from the course database.
        row = run(db.get_tournament(self.db_path, t["id"]))
        self.assertEqual(len(row["pars"].split(",")), 18)
        # Outbox row enqueued for the bot to pick up.
        outbox = run(db.poll_outbox(self.db_path))
        self.assertEqual(len(outbox), 1)
        self.assertEqual(outbox[0]["kind"], "tournament_created")
        self.assertEqual(outbox[0]["payload"]["tournament_id"], t["id"])

    def test_create_tournament_round_setting_defaults(self):
        self._crew(True)
        r = self.client.post("/api/tournaments", headers=self.h("123"),
                             json=self._create_body())
        self.assertEqual(r.status_code, 201, r.text)
        t = r.json()
        self.assertEqual(t["tee_position"], "middle")
        self.assertEqual(t["pin_position"], "white")
        self.assertEqual(t["wind_strength"], "moderate")
        self.assertEqual(t["green_speed"], "pro")

    def test_create_tournament_explicit_pars(self):
        self._crew(True)
        pars = ",".join(["4"] * 18)
        r = self.client.post("/api/tournaments", headers=self.h("123"),
                             json=self._create_body(pars=pars))
        self.assertEqual(r.status_code, 201, r.text)
        row = run(db.get_tournament(self.db_path, r.json()["id"]))
        self.assertEqual(row["pars"], pars)

    def test_create_tournament_bad_dates_400(self):
        self._crew(True)
        r = self.client.post(
            "/api/tournaments", headers=self.h("123"),
            json=self._create_body(start_date="2026-10-10",
                                   end_date="2026-10-03"))
        self.assertEqual(r.status_code, 400, r.text)

    def test_create_tournament_bad_pars_400(self):
        self._crew(True)
        r = self.client.post("/api/tournaments", headers=self.h("123"),
                             json=self._create_body(pars="4,4,4"))
        self.assertEqual(r.status_code, 400, r.text)

    def test_create_tournament_bad_format_422(self):
        self._crew(True)
        r = self.client.post("/api/tournaments", headers=self.h("123"),
                             json=self._create_body(format="stableford"))
        self.assertEqual(r.status_code, 422, r.text)

    def test_create_tournament_nine_holes_autofills_front_nine(self):
        self._crew(True)
        r = self.client.post("/api/tournaments", headers=self.h("123"),
                             json=self._create_body(holes=9))
        self.assertEqual(r.status_code, 201, r.text)
        row = run(db.get_tournament(self.db_path, r.json()["id"]))
        self.assertEqual(len(row["pars"].split(",")), 9)

    def test_create_tournament_multi_round(self):
        self._crew(True)
        rounds = [
            {"tee_position": "back", "pin_position": "black",
             "wind_strength": "severe"},
            {"tee_position": "middle", "pin_position": "white",
             "wind_strength": "moderate"},
            {"tee_position": "front", "pin_position": "red",
             "wind_strength": "low"},
        ]
        r = self.client.post("/api/tournaments", headers=self.h("123"),
                             json=self._create_body(rounds=rounds))
        self.assertEqual(r.status_code, 201, r.text)
        t = r.json()
        self.assertEqual(t["num_rounds"], 3)
        self.assertEqual(len(t["rounds"]), 3)
        self.assertEqual(
            [x["round_number"] for x in t["rounds"]], [1, 2, 3])
        self.assertEqual(t["rounds"][0]["tee_position"], "back")
        self.assertEqual(t["rounds"][2]["pin_position"], "red")
        # Round 1 copies the top-level settings too (defaults here).
        self.assertEqual(t["rounds"][0]["wind_strength"], "severe")
        db_rounds = run(db.list_rounds(self.db_path, t["id"]))
        self.assertEqual(len(db_rounds), 3)
        self.assertEqual(db_rounds[1]["tee_position"], "middle")

    def test_create_tournament_rounds_validation(self):
        self._crew(True)
        good = [{"tee_position": "middle", "pin_position": "white",
                 "wind_strength": "moderate"}]
        # 0 rounds and 6 rounds are rejected.
        for n in (0, 6):
            r = self.client.post(
                "/api/tournaments", headers=self.h("123"),
                json=self._create_body(rounds=good * n))
            self.assertEqual(r.status_code, 422, f"n={n}: {r.text}")
        # Bad per-round value rejected.
        bad = [{"tee_position": "tips", "pin_position": "white",
                "wind_strength": "moderate"}]
        r = self.client.post("/api/tournaments", headers=self.h("123"),
                             json=self._create_body(rounds=bad))
        self.assertEqual(r.status_code, 422, r.text)

    def test_create_tournament_round_dates_default_split(self):
        self._crew(True)
        rounds = [{"tee_position": "middle"}, {"tee_position": "middle"}]
        r = self.client.post("/api/tournaments", headers=self.h("123"),
                             json=self._create_body(rounds=rounds))
        self.assertEqual(r.status_code, 201, r.text)
        t = r.json()
        self.assertEqual(t["rounds"][0]["start_date"], "2026-10-03")
        self.assertEqual(t["rounds"][0]["end_date"], "2026-10-06")
        self.assertEqual(t["rounds"][1]["start_date"], "2026-10-07")
        self.assertEqual(t["rounds"][1]["end_date"], "2026-10-10")

    def test_create_tournament_round_dates_explicit_and_bad(self):
        self._crew(True)
        rounds = [
            {"tee_position": "middle",
             "start_date": "2026-10-03", "end_date": "2026-10-04"},
            {"tee_position": "middle",
             "start_date": "2026-10-09", "end_date": "2026-10-10"},
        ]
        r = self.client.post("/api/tournaments", headers=self.h("123"),
                             json=self._create_body(rounds=rounds))
        self.assertEqual(r.status_code, 201, r.text)
        t = r.json()
        self.assertEqual(t["rounds"][0]["end_date"], "2026-10-04")
        self.assertEqual(t["rounds"][1]["start_date"], "2026-10-09")
        # Round window outside the tournament window is rejected.
        bad = [{"tee_position": "middle",
                "start_date": "2026-10-01", "end_date": "2026-10-05"}]
        r = self.client.post("/api/tournaments", headers=self.h("123"),
                             json=self._create_body(rounds=bad))
        self.assertEqual(r.status_code, 400, r.text)
        # Inverted round window is rejected.
        bad = [{"tee_position": "middle",
                "start_date": "2026-10-05", "end_date": "2026-10-04"}]
        r = self.client.post("/api/tournaments", headers=self.h("123"),
                             json=self._create_body(rounds=bad))
        self.assertEqual(r.status_code, 400, r.text)

    def test_update_round_dates(self):
        self._crew(True)
        rounds = [{"tee_position": "middle"}, {"tee_position": "middle"}]
        r = self.client.post("/api/tournaments", headers=self.h("123"),
                             json=self._create_body(rounds=rounds))
        tid = r.json()["id"]
        r = self.client.patch(
            f"/api/tournaments/{tid}/rounds/2", headers=self.h("123"),
            json={"start_date": "2026-10-08", "end_date": "2026-10-09"})
        self.assertEqual(r.status_code, 200, r.text)
        rnds = r.json()["rounds"]
        self.assertEqual(rnds[1]["start_date"], "2026-10-08")
        self.assertEqual(rnds[1]["end_date"], "2026-10-09")
        # Untouched round keeps its split.
        self.assertEqual(rnds[0]["start_date"], "2026-10-03")
        # Outside the tournament window -> 400.
        r = self.client.patch(
            f"/api/tournaments/{tid}/rounds/2", headers=self.h("123"),
            json={"start_date": "2026-10-11", "end_date": "2026-10-12"})
        self.assertEqual(r.status_code, 400, r.text)
        # Not crew -> 403.
        self._crew(False)
        r = self.client.patch(
            f"/api/tournaments/{tid}/rounds/2", headers=self.h("123"),
            json={"wind_strength": "low"})
        self.assertEqual(r.status_code, 403, r.text)
        # Missing round -> 404.
        self._crew(True)
        r = self.client.patch(
            f"/api/tournaments/{tid}/rounds/9", headers=self.h("123"),
            json={"wind_strength": "low"})
        self.assertEqual(r.status_code, 404, r.text)

    def test_tee_time_round_number(self):
        self.with_tz("123")
        # Default is round 1.
        r = self._create("123")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["round_number"], 1)
        # Explicit round 2 on a single-round tournament is rejected.
        r = self._create("123", round_number=2)
        self.assertEqual(r.status_code, 422, r.text)

    def test_scorecard_round_not_started_409(self):
        self.with_tz("123")
        self.with_tz("456")
        # Round 2 opens far in the future.
        rounds = [
            {"tee_position": "middle",
             "start_date": "2026-09-20", "end_date": "2026-09-30"},
            {"tee_position": "middle",
             "start_date": "2999-01-01", "end_date": "2999-01-02"},
        ]
        self._crew(True)
        r = self.client.post("/api/tournaments", headers=self.h("123"),
                             json=self._create_body(
                                 rounds=rounds,
                                 start_date="2026-09-20",
                                 end_date="2999-01-02"))
        self.assertEqual(r.status_code, 201, r.text)
        tid = r.json()["id"]
        run(db.set_tournament_status(self.db_path, tid, "in_progress"))
        tt_id = run(db.create_tee_time(
            self.db_path, tid, "past flight",
            "2026-01-02T15:30:00+00:00", 4, "123", None))
        run(db.join_tee_time(self.db_path, tt_id, "123"))
        run(db.start_tournament_tee_time(self.db_path, tt_id, "123"))
        body = {"player_discord_id": "123", "scores": [4] * 18,
                "round_number": 2}
        r = self.client.put(f"/api/tee-times/{tt_id}/scorecard",
                            headers=self.h("123"), json=body)
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(r.json()["code"], "round_not_started")
        # Round 1 is open — submits fine.
        body["round_number"] = 1
        r = self.client.put(f"/api/tee-times/{tt_id}/scorecard",
                            headers=self.h("123"), json=body)
        self.assertEqual(r.status_code, 200, r.text)

    def test_scorecard_round_ended_cutoff(self):
        self.with_tz("123")
        self.with_tz("456")
        # Round 1 closed in the past; round 2 is open.
        rounds = [
            {"tee_position": "middle",
             "start_date": "2026-09-20", "end_date": "2026-09-25"},
            {"tee_position": "middle",
             "start_date": "2026-09-26", "end_date": "2999-01-02"},
        ]
        self._crew(True)
        r = self.client.post("/api/tournaments", headers=self.h("123"),
                             json=self._create_body(
                                 rounds=rounds,
                                 start_date="2026-09-20",
                                 end_date="2999-01-02"))
        self.assertEqual(r.status_code, 201, r.text)
        tid = r.json()["id"]
        run(db.set_tournament_status(self.db_path, tid, "in_progress"))
        tt_id = run(db.create_tee_time(
            self.db_path, tid, "past flight",
            "2026-01-02T15:30:00+00:00", 4, "123", None))
        run(db.join_tee_time(self.db_path, tt_id, "123"))
        run(db.start_tournament_tee_time(self.db_path, tt_id, "123"))
        body = {"player_discord_id": "123", "scores": [4] * 18,
                "round_number": 1}
        # Crew bypasses the cutoff.
        self._crew(True)
        r = self.client.put(f"/api/tee-times/{tt_id}/scorecard",
                            headers=self.h("123"), json=body)
        self.assertEqual(r.status_code, 200, r.text)
        # Non-crew is locked out with a clear code.
        self._crew(False)
        r = self.client.put(f"/api/tee-times/{tt_id}/scorecard",
                            headers=self.h("123"), json=body)
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(r.json()["code"], "round_ended")
        # Round 2 is still open for everyone.
        body["round_number"] = 2
        r = self.client.put(f"/api/tee-times/{tt_id}/scorecard",
                            headers=self.h("123"), json=body)
        self.assertEqual(r.status_code, 200, r.text)

    def test_tee_time_one_per_round_limit(self):
        self.with_tz("123")
        self.with_tz("456")
        rounds = [
            {"tee_position": "middle",
             "start_date": "2026-09-20", "end_date": "2026-10-06"},
            {"tee_position": "middle",
             "start_date": "2026-09-20", "end_date": "2026-10-10"},
        ]
        self._crew(True)
        r = self.client.post("/api/tournaments", headers=self.h("123"),
                             json=self._create_body(rounds=rounds,
                                                    start_date="2026-09-20"))
        self.assertEqual(r.status_code, 201, r.text)
        tid = r.json()["id"]
        run(db.set_tournament_status(self.db_path, tid, "in_progress"))

        def make_tt(uid, label, rn):
            rr = self.client.post(
                f"/api/tournaments/{tid}/tee-times", headers=self.h(uid),
                json={"label": label, "date": "2026-01-02", "time": "15:30",
                      "max_players": 4, "round_number": rn})
            self.assertEqual(rr.status_code, 200, rr.text)
            return rr.json()["id"]

        tt1 = make_tt("123", "Morning", 1)    # 123 auto-joins
        tt2 = make_tt("456", "Afternoon", 1)  # 456 auto-joins
        # 123 can't join a second round-1 tee time...
        r = self.client.post(f"/api/tee-times/{tt2}/join",
                             headers=self.h("123"))
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(r.json()["code"], "round_conflict")
        # ...or create one.
        r = self.client.post(
            f"/api/tournaments/{tid}/tee-times", headers=self.h("123"),
            json={"label": "Late", "date": "2026-01-02", "time": "16:30",
                  "max_players": 4, "round_number": 1})
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(r.json()["code"], "round_conflict")
        # A different round is fine.
        make_tt("123", "R2 flight", 2)
        # Submit 123's round-1 card in tt1.
        run(db.register_player(self.db_path, tid, "123"))
        self._start_tee_time(tt1, "123")
        body = {"player_discord_id": "123", "scores": [4] * 18,
                "round_number": 1}
        r = self.client.put(f"/api/tee-times/{tt1}/scorecard",
                            headers=self.h("123"), json=body)
        self.assertEqual(r.status_code, 200, r.text)
        # A submitted card does NOT free the round: joining the second
        # round-1 tee time still refuses.
        r = self.client.post(f"/api/tee-times/{tt2}/join",
                             headers=self.h("123"))
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(r.json()["code"], "round_conflict")

    def test_scorecard_one_per_round_per_member(self):
        # A second card for the same round is refused even from a different
        # tee time (leave the first, join the second, try to submit again).
        self.with_tz("123")
        self.with_tz("456")
        rounds = [
            {"tee_position": "middle",
             "start_date": "2026-09-20", "end_date": "2026-10-10"},
        ]
        self._crew(True)
        r = self.client.post("/api/tournaments", headers=self.h("123"),
                             json=self._create_body(rounds=rounds,
                                                    start_date="2026-09-20"))
        self.assertEqual(r.status_code, 201, r.text)
        tid = r.json()["id"]
        run(db.set_tournament_status(self.db_path, tid, "in_progress"))
        run(db.register_player(self.db_path, tid, "123"))

        def make_tt(uid, label):
            rr = self.client.post(
                f"/api/tournaments/{tid}/tee-times", headers=self.h(uid),
                json={"label": label, "date": "2026-01-02", "time": "15:30",
                      "max_players": 4, "round_number": 1})
            self.assertEqual(rr.status_code, 200, rr.text)
            return rr.json()["id"]

        tt1 = make_tt("123", "Morning")   # 123 auto-joins
        tt2 = make_tt("456", "Afternoon")  # 456 auto-joins
        self._start_tee_time(tt1, "123")
        body = {"player_discord_id": "123", "scores": [4] * 18,
                "round_number": 1}
        r = self.client.put(f"/api/tee-times/{tt1}/scorecard",
                            headers=self.h("123"), json=body)
        self.assertEqual(r.status_code, 200, r.text)
        # Leave the first tee time and join the second: allowed now...
        r = self.client.post(f"/api/tee-times/{tt1}/leave",
                             headers=self.h("123"))
        self.assertEqual(r.status_code, 200, r.text)
        # A second card for the same round is refused even from a different
        # tee time (leave the first, join the second, try to submit again).
        r = self.client.post(f"/api/tee-times/{tt2}/join",
                             headers=self.h("123"))
        self.assertEqual(r.status_code, 200, r.text)
        # ...but submitting a second round-1 card is refused.
        r = self.client.put(f"/api/tee-times/{tt2}/scorecard",
                            headers=self.h("123"), json=body)
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(r.json()["code"], "round_already_submitted")

    def test_scorecard_submit_second_tee_time(self):
        # Regression: a player in tee times for two different rounds can
        # submit to the second one (used to 403 with not_in_tee_time).
        self.with_tz("123")
        self.with_tz("456")
        rounds = [
            {"tee_position": "middle",
             "start_date": "2026-09-20", "end_date": "2026-10-10"},
            {"tee_position": "middle",
             "start_date": "2026-09-20", "end_date": "2026-10-10"},
        ]
        self._crew(True)
        r = self.client.post("/api/tournaments", headers=self.h("123"),
                             json=self._create_body(rounds=rounds,
                                                    start_date="2026-09-20"))
        self.assertEqual(r.status_code, 201, r.text)
        tid = r.json()["id"]
        run(db.set_tournament_status(self.db_path, tid, "in_progress"))
        r = self.client.post(
            f"/api/tournaments/{tid}/tee-times", headers=self.h("123"),
            json={"label": "R1", "date": "2026-01-02", "time": "15:30",
                  "max_players": 4, "round_number": 1})
        tt1 = r.json()["id"]
        r = self.client.post(
            f"/api/tournaments/{tid}/tee-times", headers=self.h("456"),
            json={"label": "R2", "date": "2026-01-03", "time": "15:30",
                  "max_players": 4, "round_number": 2})
        tt2 = r.json()["id"]
        # 123 joins the round-2 tee time (different round: allowed).
        r = self.client.post(f"/api/tee-times/{tt2}/join",
                             headers=self.h("123"))
        self.assertEqual(r.status_code, 200, r.text)
        # Submitting the round-2 card to the round-2 tee time works even
        # though tt1 was joined first.
        run(db.register_player(self.db_path, tid, "123"))
        self._start_tee_time(tt2, "456")
        body = {"player_discord_id": "123", "scores": [4] * 18,
                "round_number": 2}
        r = self.client.put(f"/api/tee-times/{tt2}/scorecard",
                            headers=self.h("123"), json=body)
        self.assertEqual(r.status_code, 200, r.text)

    def test_scorecard_witness_round_trip(self):
        self.with_tz("123")
        self.with_tz("456")
        tt_id = self._past_tee_time(self.t_open, creator="123", extra_players=["456"])
        body = {"player_discord_id": "123", "scores": [4] * 18,
                "witness_name": "  Tank ", "complete": True}
        r = self.client.put(f"/api/tee-times/{tt_id}/scorecard",
                            headers=self.h("123"), json=body)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["card"]["witness_name"], "Tank")
        r = self.client.get(f"/api/tee-times/{tt_id}/scorecard",
                            headers=self.h("123"))
        self.assertEqual(r.json()["card"]["witness_name"], "Tank")

    def test_create_tournament_match_and_holes_validation(self):
        self._crew(True)
        good = [{"tee_position": "middle", "pin_position": "white",
                 "wind_strength": "moderate"}]
        # Match play is single-round.
        r = self.client.post(
            "/api/tournaments", headers=self.h("123"),
            json=self._create_body(format="match", rounds=good * 2))
        self.assertEqual(r.status_code, 422, r.text)
        # Multi-round must be 18 holes per round.
        r = self.client.post(
            "/api/tournaments", headers=self.h("123"),
            json=self._create_body(holes=9, rounds=good * 2))
        self.assertEqual(r.status_code, 422, r.text)
        # A single explicit round works and is the default shape.
        r = self.client.post("/api/tournaments", headers=self.h("123"),
                             json=self._create_body(rounds=good))
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(r.json()["num_rounds"], 1)

    def test_scorecard_multi_round_get_filter(self):
        self.with_tz("123")
        self.with_tz("456")
        tid = run(db.create_tournament(
            self.db_path, GUILD, "Two Rounds", "stroke", 18,
            "Pebble Beach Golf Links", PARS_18, "mr", "1",
            rounds=[
                {"tee_position": "back", "pin_position": "black",
                 "wind_strength": "severe"},
                {"tee_position": "middle", "pin_position": "white",
                 "wind_strength": "moderate"},
            ]))
        tt_id = self._past_tee_time(tid, creator="123", extra_players=["456"])
        for rn, score in ((1, 4), (2, 5)):
            body = {"player_discord_id": "123", "scores": [score] * 18,
                    "round_number": rn}
            r = self.client.put(f"/api/tee-times/{tt_id}/scorecard",
                                headers=self.h("123"), json=body)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["card"]["round_number"], rn)
        # Round filter picks the right card; unfiltered returns the latest.
        r = self.client.get(f"/api/tee-times/{tt_id}/scorecard?round_number=1",
                            headers=self.h("123"))
        self.assertEqual(r.json()["card"]["total"], 72)
        r = self.client.get(f"/api/tee-times/{tt_id}/scorecard?round_number=2",
                            headers=self.h("123"))
        self.assertEqual(r.json()["card"]["total"], 90)
        r = self.client.get(f"/api/tee-times/{tt_id}/scorecard",
                            headers=self.h("123"))
        self.assertEqual(r.json()["card"]["total"], 90)

    def test_scorecard_bad_round_422(self):
        self.with_tz("123")
        tt_id = self._past_tee_time(self.t_open, creator="123")
        body = {"player_discord_id": "123", "scores": [4] * 18,
                "round_number": 2}
        r = self.client.put(f"/api/tee-times/{tt_id}/scorecard",
                            headers=self.h("123"), json=body)
        self.assertEqual(r.status_code, 422, r.text)

    def test_leaderboard_multi_round_aggregation(self):
        self.with_tz("123")
        self.with_tz("456")
        tid = run(db.create_tournament(
            self.db_path, GUILD, "Two Rounds", "stroke", 18,
            "Pebble Beach Golf Links", PARS_18, "mr", "1",
            rounds=[
                {"tee_position": "back", "pin_position": "black",
                 "wind_strength": "severe"},
                {"tee_position": "middle", "pin_position": "white",
                 "wind_strength": "moderate"},
            ]))
        tt_id = self._past_tee_time(tid, creator="123", extra_players=["456"])
        # 123: 72 + 90 = 162 (+18). 456: 76 + 76 = 152 (+8) — wins on to-par.
        run(db.upsert_scorecard(self.db_path, tid, "123", None, tt_id,
                                [4] * 18, "verified", round_number=1))
        run(db.upsert_scorecard(self.db_path, tid, "123", None, tt_id,
                                [5] * 18, "verified", round_number=2))
        run(db.upsert_scorecard(self.db_path, tid, "456", None, tt_id,
                                [4] * 17 + [8], "verified", round_number=1))
        run(db.upsert_scorecard(self.db_path, tid, "456", None, tt_id,
                                [4] * 17 + [8], "verified", round_number=2))
        r = self.client.get(f"/api/tournaments/{tid}/leaderboard",
                            headers=self.h("123"))
        self.assertEqual(r.status_code, 200, r.text)
        lb = r.json()
        self.assertEqual(lb["num_rounds"], 2)
        self.assertEqual(len(lb["rounds"]), 2)
        rows = lb["standings"]
        self.assertEqual([x["discord_id"] for x in rows], ["456", "123"])
        self.assertEqual(rows[0]["total"], 152)
        self.assertEqual(rows[0]["to_par"], 8)
        self.assertEqual(rows[0]["rounds_played"], 2)
        self.assertEqual([d["round_number"] for d in rows[0]["rounds"]],
                         [1, 2])
        self.assertEqual(rows[0]["rounds"][0]["total"], 76)
        self.assertEqual(rows[1]["total"], 162)
        self.assertEqual(rows[1]["to_par"], 18)

    def test_leaderboard_multi_round_partial(self):
        # A player who only finished round 1 still ranks, on to-par.
        self.with_tz("123")
        self.with_tz("456")
        tid = run(db.create_tournament(
            self.db_path, GUILD, "Two Rounds", "stroke", 18,
            "Pebble Beach Golf Links", PARS_18, "mr", "1",
            rounds=[
                {"tee_position": "back", "pin_position": "black",
                 "wind_strength": "severe"},
                {"tee_position": "middle", "pin_position": "white",
                 "wind_strength": "moderate"},
            ]))
        tt_id = self._past_tee_time(tid, creator="123", extra_players=["456"])
        run(db.upsert_scorecard(self.db_path, tid, "123", None, tt_id,
                                [4] * 18, "verified", round_number=1))
        run(db.upsert_scorecard(self.db_path, tid, "456", None, tt_id,
                                [5] * 18, "verified", round_number=1))
        run(db.upsert_scorecard(self.db_path, tid, "456", None, tt_id,
                                [5] * 18, "verified", round_number=2))
        r = self.client.get(f"/api/tournaments/{tid}/leaderboard",
                            headers=self.h("123"))
        rows = r.json()["standings"]
        # 123 is E through 1 round; 456 is +36 through 2 — 123 leads.
        self.assertEqual([x["discord_id"] for x in rows], ["123", "456"])
        self.assertEqual(rows[0]["rounds_played"], 1)
        self.assertEqual(rows[1]["rounds_played"], 2)

    # -- profile crew flag ---------------------------------------------
    def test_me_includes_is_crew(self):
        self._crew(True)
        r = self.client.get("/api/players/me", headers=self.h("123"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["is_crew"])
        self._crew(False)
        r = self.client.get("/api/players/me", headers=self.h("123"))
        self.assertFalse(r.json()["is_crew"])

    # -- tournament management (edit/end/complete/delete) ------------------
    def _admin(self, value):
        async def fake(discord_id):
            return value
        main.fetch_admin_status = fake

    def _mod_admin(self, value):
        async def fake(discord_id):
            return value
        main.fetch_mod_admin_status = fake

    def _make_open(self, name="Mgmt Open"):
        return run(
            db.create_tournament(
                self.db_path, GUILD, name, "stroke", 18,
                "Pebble Beach Golf Links", None, None, "1",
                start_date="2026-10-03", end_date="2026-10-10",
            )
        )

    def test_edit_tournament_ok(self):
        self._crew(True)
        tid = self._make_open()
        r = self.client.patch(
            f"/api/tournaments/{tid}", headers=self.h("1"),
            json={"name": "Renamed", "course": "Spyglass Hill"})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["name"], "Renamed")
        self.assertEqual(body["course"], "Spyglass Hill")
        self.assertEqual(body["format"], "stroke")  # untouched

    def test_edit_tournament_empty_422(self):
        self._crew(True)
        tid = self._make_open()
        r = self.client.patch(
            f"/api/tournaments/{tid}", headers=self.h("1"), json={})
        self.assertEqual(r.status_code, 422, r.text)

    def test_edit_tournament_bad_date_422(self):
        self._crew(True)
        tid = self._make_open()
        r = self.client.patch(
            f"/api/tournaments/{tid}", headers=self.h("1"),
            json={"start_date": "not-a-date"})
        self.assertEqual(r.status_code, 422, r.text)

    def test_edit_tournament_bad_range_400(self):
        self._crew(True)
        tid = self._make_open()
        r = self.client.patch(
            f"/api/tournaments/{tid}", headers=self.h("1"),
            json={"start_date": "2026-10-10", "end_date": "2026-10-03"})
        self.assertEqual(r.status_code, 400, r.text)

    def test_edit_tournament_not_crew_403(self):
        self._crew(False)
        tid = self._make_open()
        r = self.client.patch(
            f"/api/tournaments/{tid}", headers=self.h("1"),
            json={"name": "Nope"})
        self.assertEqual(r.status_code, 403, r.text)

    def test_edit_tournament_404(self):
        self._crew(True)
        r = self.client.patch(
            "/api/tournaments/99999", headers=self.h("1"),
            json={"name": "Nope"})
        self.assertEqual(r.status_code, 404, r.text)

    def test_complete_tournament(self):
        self._crew(True)
        tid = self._make_open()
        r = self.client.post(
            f"/api/tournaments/{tid}/complete", headers=self.h("1"))
        self.assertEqual(r.status_code, 200, r.text)
        t = run(db.get_tournament(self.db_path, tid))
        self.assertEqual(t["status"], "completed")
        rows = run(db.poll_outbox(self.db_path))
        kinds = [row["kind"] for row in rows]
        self.assertIn("tournament_completed", kinds)

    def test_complete_tournament_twice_409(self):
        self._crew(True)
        tid = self._make_open()
        r = self.client.post(
            f"/api/tournaments/{tid}/complete", headers=self.h("1"))
        self.assertEqual(r.status_code, 200, r.text)
        r = self.client.post(
            f"/api/tournaments/{tid}/complete", headers=self.h("1"))
        self.assertEqual(r.status_code, 409, r.text)

    def test_complete_tournament_not_crew_403(self):
        self._crew(False)
        tid = self._make_open()
        r = self.client.post(
            f"/api/tournaments/{tid}/complete", headers=self.h("1"))
        self.assertEqual(r.status_code, 403, r.text)

    def test_end_tournament(self):
        self._crew(True)
        tid = self._make_open()
        r = self.client.post(
            f"/api/tournaments/{tid}/end", headers=self.h("1"))
        self.assertEqual(r.status_code, 200, r.text)
        t = run(db.get_tournament(self.db_path, tid))
        self.assertEqual(t["status"], "completed")
        rows = run(db.poll_outbox(self.db_path))
        kinds = [row["kind"] for row in rows]
        self.assertIn("tournament_ended", kinds)
        self.assertNotIn("tournament_completed", kinds)

    def test_delete_tournament_admin_ok(self):
        self._crew(True)
        self._admin(True)
        tid = self._make_open()
        r = self.client.delete(
            f"/api/tournaments/{tid}", headers=self.h("1"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["deleted"])
        self.assertIsNone(run(db.get_tournament(self.db_path, tid)))
        # gone from the tournament list too
        r = self.client.get("/api/tournaments", headers=self.h("1"))
        self.assertEqual(r.status_code, 200, r.text)
        ids = [t["id"] for t in r.json()]
        self.assertNotIn(tid, ids)

    def test_delete_tournament_crew_not_admin_403(self):
        self._crew(True)
        self._admin(False)
        tid = self._make_open()
        r = self.client.delete(
            f"/api/tournaments/{tid}", headers=self.h("1"))
        self.assertEqual(r.status_code, 403, r.text)
        self.assertIsNotNone(run(db.get_tournament(self.db_path, tid)))

    def test_delete_tournament_404(self):
        self._crew(True)
        self._admin(True)
        r = self.client.delete("/api/tournaments/99999", headers=self.h("1"))
        self.assertEqual(r.status_code, 404, r.text)

    def test_me_includes_is_admin(self):
        self._admin(True)
        r = self.client.get("/api/players/me", headers=self.h("123"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["is_admin"])
        self._admin(False)
        r = self.client.get("/api/players/me", headers=self.h("123"))
        self.assertFalse(r.json()["is_admin"])


class AltShotApiTestCase(ApiTestCase):
    """Alt-shot records: tee times, teams (2-4 players, optional team name),
    scores, and per-team-size record leaderboards."""

    def _create_tt(self, uid="1", **over):
        body = {
            "label": "Sat alt-shot",
            "course": "Pebble Beach Golf Links",
            "starts_at": "2030-10-03T14:00:00Z",
            "max_teams": 2,
        }
        body.update(over)
        r = self.client.post("/api/altshot-tee-times", headers=self.h(uid),
                             json=body)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def _teams_by_number(self, tt):
        return {t["team_number"]: t for t in tt["teams"]}

    def _fill_two_team(self, tt, team_size, uids):
        """Fill a fixed 2-team tee time via the team picker: uids[0] is the
        creator (already on team 1); the rest fill team 1 then team 2.
        Returns the refreshed tee time."""
        teams = self._teams_by_number(tt)
        t1, t2 = teams[1], teams[2]
        need1 = team_size - len(t1["member_discord_ids"])
        need2 = team_size - len(t2["member_discord_ids"])
        rest = list(uids[1:])
        for uid in rest[:need1]:
            r = self.client.post(
                f"/api/altshot-tee-times/{tt['id']}/join",
                headers=self.h(uid), json={"team_id": t1["id"]})
            self.assertEqual(r.status_code, 200, r.text)
        for uid in rest[need1:need1 + need2]:
            r = self.client.post(
                f"/api/altshot-tee-times/{tt['id']}/join",
                headers=self.h(uid), json={"team_id": t2["id"]})
            self.assertEqual(r.status_code, 200, r.text)
        r = self.client.get(f"/api/altshot-tee-times/{tt['id']}",
                            headers=self.h(uids[0]))
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_create_and_list(self):
        tt = self._create_tt()
        # Fixed two teams: team 1 holds the creator, team 2 starts empty.
        self.assertEqual(len(tt["teams"]), 2)
        teams = self._teams_by_number(tt)
        t1, t2 = teams[1], teams[2]
        self.assertEqual(t1["player1_discord_id"], "1")
        self.assertEqual(t1["member_discord_ids"], ["1"])
        self.assertEqual(t1["team_size"], 1)
        self.assertEqual(t2["member_discord_ids"], [])
        self.assertEqual(t2["team_size"], 0)
        r = self.client.get("/api/altshot-tee-times", headers=self.h("1"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(len(r.json()["tee_times"]), 1)

    def test_create_ignores_team_names(self):
        # No team names in alt-shot: name fields are ignored (not 422),
        # no team_name key is returned, and displays are structural.
        tt = self._create_tt(team1_name="Eagles", team2_name="Falcons")
        teams = self._teams_by_number(tt)
        self.assertNotIn("team_name", teams[1])
        self.assertNotIn("team_name", teams[2])
        self.assertEqual(teams[1]["display_name"], "Team 1")
        self.assertEqual(teams[2]["display_name"], "Team 2")
        self.assertEqual(teams[2]["member_discord_ids"], [])
        # The same "name" on another tee time is fine — nothing is
        # claimed.
        tt2 = self._create_tt(uid="3", team1_name="Eagles")
        self.assertNotIn("team_name", self._teams_by_number(tt2)[1])
        r = self.client.get("/api/altshot-tee-times", headers=self.h("3"))
        self.assertEqual(len(r.json()["tee_times"]), 2)

    def test_create_bad_course_422(self):
        r = self.client.post("/api/altshot-tee-times", headers=self.h("1"),
                             json={"label": "x", "course": "Fake CC"})
        self.assertEqual(r.status_code, 422, r.text)

    def test_create_bad_max_teams_422(self):
        r = self.client.post(
            "/api/altshot-tee-times", headers=self.h("1"),
            json={"label": "x", "course": "Pebble Beach Golf Links",
                  "max_teams": 3})
        self.assertEqual(r.status_code, 422, r.text)

    def test_create_fixed_team_size(self):
        # 1-team tee times require a roster size of 2-4.
        for bad in (None, 1, 5):
            body = {"label": "x", "course": "Pebble Beach Golf Links",
                    "max_teams": 1, "team_size": bad}
            r = self.client.post("/api/altshot-tee-times",
                                 headers=self.h("1"), json=body)
            self.assertEqual(r.status_code, 422, r.text)
        body = {"label": "x", "course": "Pebble Beach Golf Links",
                "max_teams": 1, "team_size": 3,
                "starts_at": "2030-10-03T14:00:00Z"}
        r = self.client.post("/api/altshot-tee-times", headers=self.h("1"),
                             json=body)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["team_size"], 3)
        # team_size applies to both teams of a 2-team tee time.
        r = self.client.post(
            "/api/altshot-tee-times", headers=self.h("1"),
            json={"label": "x", "course": "Pebble Beach Golf Links",
                  "starts_at": "2030-10-03T14:00:00Z",
                  "max_teams": 2, "team_size": 3})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["team_size"], 3)
        # Default team size is 2.
        r = self.client.post(
            "/api/altshot-tee-times", headers=self.h("1"),
            json={"label": "x", "course": "Pebble Beach Golf Links",
                  "starts_at": "2030-10-03T14:00:00Z", "max_teams": 2})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["team_size"], 2)

    def test_join_full_409(self):
        tt = self._create_tt()
        teams = self._teams_by_number(tt)
        t2 = teams[2]
        # Joining picks a team; only registered players, no typed names.
        r = self.client.post(f"/api/altshot-tee-times/{tt['id']}/join",
                             headers=self.h("2"), json={"team_id": t2["id"]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self._teams_by_number(r.json())[2][
            "member_discord_ids"], ["2"])
        # A second player fills the team (default size 2)...
        r = self.client.post(f"/api/altshot-tee-times/{tt['id']}/join",
                             headers=self.h("3"), json={"team_id": t2["id"]})
        self.assertEqual(r.status_code, 200, r.text)
        # ...and a third is rejected.
        r = self.client.post(f"/api/altshot-tee-times/{tt['id']}/join",
                             headers=self.h("4"), json={"team_id": t2["id"]})
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn("full", r.json()["detail"].lower())
        # Joining without picking a team is rejected.
        r = self.client.post(f"/api/altshot-tee-times/{tt['id']}/join",
                             headers=self.h("5"), json={})
        self.assertEqual(r.status_code, 422, r.text)
        # Guest text names are rejected on 2-team tee times.
        r = self.client.post(f"/api/altshot-tee-times/{tt['id']}/join",
                             headers=self.h("5"),
                             json={"team_id": teams[1]["id"],
                                   "extra_names": ["Gus"]})
        self.assertEqual(r.status_code, 422, r.text)

    def test_player_cannot_join_both_teams(self):
        tt = self._create_tt()
        teams = self._teams_by_number(tt)
        r = self.client.post(f"/api/altshot-tee-times/{tt['id']}/join",
                             headers=self.h("2"),
                             json={"team_id": teams[2]["id"]})
        self.assertEqual(r.status_code, 200, r.text)
        # Already on team 2: joining team 1 is an idempotent no-op — the
        # player stays on team 2 only.
        r = self.client.post(f"/api/altshot-tee-times/{tt['id']}/join",
                             headers=self.h("2"),
                             json={"team_id": teams[1]["id"]})
        self.assertEqual(r.status_code, 200, r.text)
        teams = self._teams_by_number(r.json())
        self.assertEqual(teams[2]["member_discord_ids"], ["2"])
        self.assertEqual(teams[1]["member_discord_ids"], ["1"])

    def test_join_idempotent(self):
        tt = self._create_tt()
        t2 = self._teams_by_number(tt)[2]
        for _ in range(2):
            r = self.client.post(f"/api/altshot-tee-times/{tt['id']}/join",
                                 headers=self.h("2"),
                                 json={"team_id": t2["id"]})
            self.assertEqual(r.status_code, 200, r.text)
        teams = self._teams_by_number(r.json())
        self.assertEqual(len(teams), 2)
        self.assertEqual(teams[2]["member_discord_ids"], ["2"])

    def _team_url(self, tt_id, team_id, suffix=""):
        return (f"/api/altshot-tee-times/{tt_id}/teams/{team_id}" + suffix)

    def test_update_ignores_team_names(self):
        tt = self._create_tt()
        team = self._teams_by_number(tt)[1]
        # Team names are ignored: the display stays structural.
        r = self.client.patch(self._team_url(tt["id"], team["id"]),
                              headers=self.h("1"),
                              json={"team_name": "The Eagles"})
        self.assertEqual(r.status_code, 200, r.text)
        t = self._teams_by_number(r.json())[1]
        self.assertNotIn("team_name", t)
        self.assertEqual(t["display_name"], "Team 1")
        # Typed-in guest names are rejected on fixed 2-team tee times.
        r = self.client.patch(self._team_url(tt["id"], team["id"]),
                              headers=self.h("1"),
                              json={"extra_names": ["Dave"]})
        self.assertEqual(r.status_code, 422, r.text)
        # A stranger may not edit teams.
        r = self.client.patch(self._team_url(tt["id"], team["id"]),
                              headers=self.h("9"),
                              json={"move_discord_id": "1"})
        self.assertEqual(r.status_code, 403, r.text)

    def test_switch_team_before_scoring(self):
        tt = self._create_tt()
        teams = self._teams_by_number(tt)
        t1, t2 = teams[1], teams[2]
        # User 2 joins team 2, then switches to team 1 (room for one more).
        self.client.post(f"/api/altshot-tee-times/{tt['id']}/join",
                         headers=self.h("2"), json={"team_id": t2["id"]})
        r = self.client.post(
            f"/api/altshot-tee-times/{tt['id']}/switch-team",
            headers=self.h("2"), json={"team_id": t1["id"]})
        self.assertEqual(r.status_code, 200, r.text)
        teams = self._teams_by_number(r.json())
        self.assertEqual(teams[1]["member_discord_ids"], ["1", "2"])
        self.assertEqual(teams[2]["member_discord_ids"], [])
        # Fill team 2, then fill team 1: switching to a full team fails.
        self.client.post(f"/api/altshot-tee-times/{tt['id']}/join",
                         headers=self.h("3"), json={"team_id": t2["id"]})
        self.client.post(f"/api/altshot-tee-times/{tt['id']}/join",
                         headers=self.h("4"), json={"team_id": t2["id"]})
        r = self.client.post(
            f"/api/altshot-tee-times/{tt['id']}/switch-team",
            headers=self.h("2"), json={"team_id": t2["id"]})
        self.assertEqual(r.status_code, 409, r.text)
        # Once a score is submitted the teams lock.
        r = self.client.post(self._team_url(tt["id"], t1["id"], "/score"),
                             headers=self.h("1"), json={"holes": [4] * 18})
        self.assertEqual(r.status_code, 200, r.text)
        r = self.client.post(
            f"/api/altshot-tee-times/{tt['id']}/switch-team",
            headers=self.h("3"), json={"team_id": t1["id"]})
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn("locked", r.json()["detail"].lower())

    def test_organizer_moves_and_removes_players(self):
        tt = self._create_tt(team_size=3)
        teams = self._teams_by_number(tt)
        t1, t2 = teams[1], teams[2]
        self.client.post(f"/api/altshot-tee-times/{tt['id']}/join",
                         headers=self.h("2"), json={"team_id": t1["id"]})
        for uid in ("4", "5"):
            self.client.post(f"/api/altshot-tee-times/{tt['id']}/join",
                             headers=self.h(uid), json={"team_id": t2["id"]})
        # Organizer moves user 4 onto team 1 (team 1 now full).
        r = self.client.patch(self._team_url(tt["id"], t1["id"]),
                              headers=self.h("1"),
                              json={"move_discord_id": "4"})
        self.assertEqual(r.status_code, 200, r.text)
        teams = self._teams_by_number(r.json())
        self.assertEqual(teams[1]["member_discord_ids"], ["1", "2", "4"])
        self.assertEqual(teams[2]["member_discord_ids"], ["5"])
        # Team 1 is full: moving user 5 there fails.
        r = self.client.patch(self._team_url(tt["id"], t1["id"]),
                              headers=self.h("1"),
                              json={"move_discord_id": "5"})
        self.assertEqual(r.status_code, 409, r.text)
        # Organizer removes user 5 from team 2.
        r = self.client.patch(self._team_url(tt["id"], t2["id"]),
                              headers=self.h("1"),
                              json={"remove_discord_id": "5"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(
            self._teams_by_number(r.json())[2]["member_discord_ids"], [])
        # A non-organizer, non-crew player may not manage teams.
        r = self.client.patch(self._team_url(tt["id"], t1["id"]),
                              headers=self.h("5"),
                              json={"remove_discord_id": "3"})
        self.assertEqual(r.status_code, 403, r.text)

    def test_team_locks_after_scoring(self):
        tt = self._create_tt()
        tt = self._fill_two_team(tt, 2, ["1", "2", "3", "4"])
        teams = self._teams_by_number(tt)
        t1, t2 = teams[1], teams[2]
        r = self.client.post(self._team_url(tt["id"], t1["id"], "/score"),
                             headers=self.h("1"), json={"holes": [4] * 18})
        self.assertEqual(r.status_code, 200, r.text)
        # Moves and removals are blocked once scoring has started...
        r = self.client.patch(self._team_url(tt["id"], t1["id"]),
                              headers=self.h("1"),
                              json={"move_discord_id": "3"})
        self.assertEqual(r.status_code, 409, r.text)
        r = self.client.patch(self._team_url(tt["id"], t2["id"]),
                              headers=self.h("1"),
                              json={"remove_discord_id": "3"})
        self.assertEqual(r.status_code, 409, r.text)

    def test_submit_needs_both_teams_full(self):
        tt = self._create_tt()
        teams = self._teams_by_number(tt)
        t1 = teams[1]
        # Team 2 is empty: scoring is blocked even for the creator's team.
        r = self.client.post(self._team_url(tt["id"], t1["id"], "/score"),
                             headers=self.h("1"),
                             json={"holes": [4] * 18})
        self.assertEqual(r.status_code, 422, r.text)
        self.assertIn("both teams", r.json()["detail"].lower())
        # Fill team 1 only: still blocked.
        self.client.post(f"/api/altshot-tee-times/{tt['id']}/join",
                         headers=self.h("2"), json={"team_id": t1["id"]})
        r = self.client.post(self._team_url(tt["id"], t1["id"], "/score"),
                             headers=self.h("1"),
                             json={"holes": [4] * 18})
        self.assertEqual(r.status_code, 422, r.text)
        # Fill team 2: now either team may submit.
        self.client.post(f"/api/altshot-tee-times/{tt['id']}/join",
                         headers=self.h("3"),
                         json={"team_id": teams[2]["id"]})
        self.client.post(f"/api/altshot-tee-times/{tt['id']}/join",
                         headers=self.h("4"),
                         json={"team_id": teams[2]["id"]})
        r = self.client.post(self._team_url(tt["id"], t1["id"], "/score"),
                             headers=self.h("1"),
                             json={"holes": [4] * 18})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["total"], 72)

    def test_submit_bad_holes_422(self):
        tt = self._create_tt()
        tt = self._fill_two_team(tt, 2, ["1", "2", "3", "4"])
        teams = self._teams_by_number(tt)
        r = self.client.post(self._team_url(tt["id"], teams[1]["id"],
                                             "/score"),
                             headers=self.h("1"),
                             json={"holes": [4] * 9})
        self.assertEqual(r.status_code, 422, r.text)

    def test_team_edit_forbidden_for_stranger(self):
        tt = self._create_tt()
        team = self._teams_by_number(tt)[1]
        r = self.client.patch(self._team_url(tt["id"], team["id"]),
                              headers=self.h("9"),
                              json={"extra_names": ["Mallory"]})
        self.assertEqual(r.status_code, 403, r.text)
        r = self.client.post(self._team_url(tt["id"], team["id"], "/score"),
                             headers=self.h("9"),
                             json={"holes": [4] * 18})
        self.assertEqual(r.status_code, 403, r.text)

    def test_fixed_roster_join_cap_and_submit(self):
        tt = self._create_tt(max_teams=1, team_size=2)
        team = tt["teams"][0]
        # User 2 joins the single team's roster.
        r = self.client.post(f"/api/altshot-tee-times/{tt['id']}/join",
                             headers=self.h("2"), json={})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(len(r.json()["teams"]), 1)
        self.assertEqual(r.json()["teams"][0]["team_size"], 2)
        # A third player is rejected.
        r = self.client.post(f"/api/altshot-tee-times/{tt['id']}/join",
                             headers=self.h("3"), json={})
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn("full", r.json()["detail"].lower())
        # Full roster submits fine.
        r = self.client.post(self._team_url(tt["id"], team["id"], "/score"),
                             headers=self.h("2"),
                             json={"holes": [4] * 18})
        self.assertEqual(r.status_code, 200, r.text)
        # Leaving frees the roster spot; a member re-submit is now refused
        # by the mod/admin gate before the roster check even runs.
        self.client.post(f"/api/altshot-tee-times/{tt['id']}/leave",
                         headers=self.h("2"))
        r = self.client.post(self._team_url(tt["id"], team["id"], "/score"),
                             headers=self.h("1"),
                             json={"holes": [4] * 18})
        self.assertEqual(r.status_code, 403, r.text)
        # Re-submitting a score is mod/admin only.
        self.client.post(f"/api/altshot-tee-times/{tt['id']}/join",
                         headers=self.h("2"), json={})
        r = self.client.post(self._team_url(tt["id"], team["id"], "/score"),
                             headers=self.h("2"),
                             json={"holes": [3] * 18})
        self.assertEqual(r.status_code, 403, r.text)
        self._mod_admin(True)
        r = self.client.post(self._team_url(tt["id"], team["id"], "/score"),
                             headers=self.h("2"),
                             json={"holes": [3] * 18})
        self.assertEqual(r.status_code, 200, r.text)

    def test_fixed_roster_text_names_count_toward_cap(self):
        tt = self._create_tt(max_teams=1, team_size=3)
        team = tt["teams"][0]
        r = self.client.patch(self._team_url(tt["id"], team["id"]),
                              headers=self.h("1"),
                              json={"extra_names": ["Zed", "Yara", "Xio"]})
        self.assertEqual(r.status_code, 422, r.text)
        r = self.client.patch(self._team_url(tt["id"], team["id"]),
                              headers=self.h("1"),
                              json={"extra_names": ["Zed"]})
        self.assertEqual(r.status_code, 200, r.text)
        # Registered joins still fill the remaining spots.
        r = self.client.post(f"/api/altshot-tee-times/{tt['id']}/join",
                             headers=self.h("2"), json={})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["teams"][0]["team_size"], 3)
        r = self.client.post(f"/api/altshot-tee-times/{tt['id']}/join",
                             headers=self.h("3"), json={})
        self.assertEqual(r.status_code, 409, r.text)

    def test_records_split_by_team_size(self):
        # Records default to back/black/moderate/pro setups; only
        # registered players score on 2-team tee times.
        tt = self._create_tt(tee_position="back", pin_position="black",
                             wind_strength="moderate", green_speed="pro")
        tt = self._fill_two_team(tt, 2, ["1", "2", "3", "4"])
        teams = self._teams_by_number(tt)
        r = self.client.post(self._team_url(tt["id"], teams[1]["id"],
                                             "/score"),
                             headers=self.h("1"), json={"holes": [3] * 18})
        self.assertEqual(r.status_code, 200, r.text)
        # A 4-player board on its own tee time.
        tt4 = self._create_tt(tee_position="back", pin_position="black",
                              wind_strength="moderate", green_speed="pro",
                              team_size=4)
        tt4 = self._fill_two_team(
            tt4, 4, ["1", "12", "13", "14", "15", "16", "17", "18"])
        teams4 = self._teams_by_number(tt4)
        r = self.client.post(self._team_url(tt4["id"], teams4[1]["id"],
                                             "/score"),
                             headers=self.h("1"), json={"holes": [4] * 18})
        self.assertEqual(r.status_code, 200, r.text)
        # 2-player board: only the duo.
        r = self.client.get("/api/altshot-records",
                            params={"course": "Pebble Beach Golf Links",
                                    "team_size": 2},
                            headers=self.h("1"))
        self.assertEqual(r.status_code, 200, r.text)
        recs = r.json()["records"]
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["total"], 54)
        self.assertEqual(recs[0]["to_par"], -18)
        self.assertEqual(recs[0]["team_size"], 2)
        self.assertEqual(recs[0]["team_display"], "User1 & User2")
        self.assertNotIn("team_name", recs[0])
        # Read-only scorecard data ships with each record.
        self.assertEqual(recs[0]["holes"], [3] * 18)
        self.assertEqual(len(recs[0]["pars"]), 18)
        # 4-player board: only the squad, with all names visible.
        r = self.client.get("/api/altshot-records",
                            params={"course": "Pebble Beach Golf Links",
                                    "team_size": 4},
                            headers=self.h("1"))
        recs = r.json()["records"]
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["total"], 72)
        self.assertEqual(recs[0]["to_par"], 0)
        self.assertEqual(len(recs[0]["player_names"]), 4)
        # 3-player board: empty.
        r = self.client.get("/api/altshot-records",
                            params={"course": "Pebble Beach Golf Links",
                                    "team_size": 3},
                            headers=self.h("1"))
        self.assertEqual(r.json()["records"], [])
        # Bad team_size rejected.
        r = self.client.get("/api/altshot-records",
                            params={"course": "Pebble Beach Golf Links",
                                    "team_size": 5},
                            headers=self.h("1"))
        self.assertEqual(r.status_code, 422, r.text)

    def test_records_filtered_by_setup(self):
        # A record under middle/white does not show on the default
        # back/black board, but does when the filters match.
        tt = self._create_tt(tee_position="middle", pin_position="white")
        tt = self._fill_two_team(tt, 2, ["1", "2", "3", "4"])
        teams = self._teams_by_number(tt)
        r = self.client.post(self._team_url(tt["id"], teams[1]["id"],
                                             "/score"),
                             headers=self.h("1"), json={"holes": [4] * 18})
        self.assertEqual(r.status_code, 200, r.text)
        params = {"course": "Pebble Beach Golf Links", "team_size": 2}
        r = self.client.get("/api/altshot-records", params=params,
                            headers=self.h("1"))
        self.assertEqual(r.json()["records"], [])
        params.update({"tee_position": "middle", "pin_position": "white"})
        r = self.client.get("/api/altshot-records", params=params,
                            headers=self.h("1"))
        self.assertEqual(len(r.json()["records"]), 1)
        # Bad setup values rejected.
        r = self.client.get("/api/altshot-records",
                            params={"course": "Pebble Beach Golf Links",
                                    "team_size": 2,
                                    "tee_position": "sideways"},
                            headers=self.h("1"))
        self.assertEqual(r.status_code, 422, r.text)

    def test_delete_score_and_leave(self):
        tt = self._create_tt()
        tt = self._fill_two_team(tt, 2, ["1", "2", "3", "4"])
        teams = self._teams_by_number(tt)
        t1 = teams[1]
        r = self.client.post(self._team_url(tt["id"], t1["id"], "/score"),
                             headers=self.h("1"), json={"holes": [4] * 18})
        self.assertEqual(r.status_code, 200, r.text)
        # Deleting a submitted score is mod/admin only.
        r = self.client.delete(self._team_url(tt["id"], t1["id"], "/score"),
                               headers=self.h("1"))
        self.assertEqual(r.status_code, 403, r.text)
        self._crew(True)  # crew (e.g. tournament director) still can't.
        r = self.client.delete(self._team_url(tt["id"], t1["id"], "/score"),
                               headers=self.h("1"))
        self.assertEqual(r.status_code, 403, r.text)
        self._mod_admin(True)
        r = self.client.delete(self._team_url(tt["id"], t1["id"], "/score"),
                               headers=self.h("1"))
        self.assertEqual(r.status_code, 200, r.text)
        r = self.client.get("/api/altshot-records",
                            params={"course": "Pebble Beach Golf Links",
                                    "team_size": 2},
                            headers=self.h("1"))
        self.assertEqual(r.json()["records"], [])
        # Leaving frees the player's roster spot; the two pre-created
        # teams stay.
        r = self.client.post(f"/api/altshot-tee-times/{tt['id']}/leave",
                             headers=self.h("1"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(len(r.json()["teams"]), 2)
        self.assertEqual(
            self._teams_by_number(r.json())[1]["member_discord_ids"], ["2"])

    def test_delete_tee_time_forbidden_for_stranger(self):
        tt = self._create_tt()
        r = self.client.delete(f"/api/altshot-tee-times/{tt['id']}",
                               headers=self.h("9"))
        self.assertEqual(r.status_code, 403, r.text)
        r = self.client.get(f"/api/altshot-tee-times/{tt['id']}",
                            headers=self.h("1"))
        self.assertEqual(r.status_code, 200, r.text)

    def test_score_manipulation_mod_admin_only(self):
        tt = self._create_tt()
        tt = self._fill_two_team(tt, 2, ["1", "2", "3", "4"])
        teams = self._teams_by_number(tt)
        t1 = teams[1]
        # First submission stays open to the team.
        r = self.client.post(self._team_url(tt["id"], t1["id"], "/score"),
                             headers=self.h("1"), json={"holes": [4] * 18})
        self.assertEqual(r.status_code, 200, r.text)
        score_url = self._team_url(tt["id"], t1["id"], "/score")
        # Re-submitting (editing) is mod/admin only: the submitting member
        # is refused, and so is plain crew (e.g. a tournament director).
        r = self.client.post(score_url, headers=self.h("1"),
                             json={"holes": [3] * 18})
        self.assertEqual(r.status_code, 403, r.text)
        self._crew(True)
        r = self.client.post(score_url, headers=self.h("1"),
                             json={"holes": [3] * 18})
        self.assertEqual(r.status_code, 403, r.text)
        self._mod_admin(True)
        r = self.client.post(score_url, headers=self.h("1"),
                             json={"holes": [3] * 18})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["total"], 54)
        # /api/players/me exposes the flag.
        r = self.client.get("/api/players/me", headers=self.h("1"))
        self.assertTrue(r.json()["can_manage_scores"])
        self._mod_admin(False)
        r = self.client.get("/api/players/me", headers=self.h("1"))
        self.assertFalse(r.json()["can_manage_scores"])

    def test_404s(self):
        r = self.client.get("/api/altshot-tee-times/nope", headers=self.h("1"))
        self.assertEqual(r.status_code, 404, r.text)
        r = self.client.post("/api/altshot-tee-times/nope/join",
                             headers=self.h("1"), json={})
        self.assertEqual(r.status_code, 404, r.text)


class AltShotRecordsSummaryApiTestCase(ApiTestCase):
    """GET /api/altshot-records/summary: one batched best-record-per-course
    lookup for the tee-time course picker."""

    def _create_tt(self, uid="1", course="Pebble Beach Golf Links",
                   team_size=2, **over):
        body = {
            "label": "Sat alt-shot",
            "course": course,
            "starts_at": "2030-10-03T14:00:00Z",
            "max_teams": 2,
            "team_size": team_size,
            "tee_position": "back",
            "pin_position": "black",
            "wind_strength": "moderate",
            "green_speed": "pro",
        }
        body.update(over)
        r = self.client.post("/api/altshot-tee-times", headers=self.h(uid),
                             json=body)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def _team(self, tt, number):
        return next(t for t in tt["teams"] if t["team_number"] == number)

    def _fill_and_score(self, course, team_size, holes, uids, **setup):
        """Create a fixed 2-team tee time, fill both teams with registered
        players, and submit team 1's score."""
        tt = self._create_tt(uids[0], course=course, team_size=team_size,
                             **setup)
        t1, t2 = self._team(tt, 1), self._team(tt, 2)
        for uid in uids[1:team_size]:  # team 1 already has the creator
            r = self.client.post(
                f"/api/altshot-tee-times/{tt['id']}/join",
                headers=self.h(uid), json={"team_id": t1["id"]})
            self.assertEqual(r.status_code, 200, r.text)
        for uid in uids[team_size:2 * team_size]:
            r = self.client.post(
                f"/api/altshot-tee-times/{tt['id']}/join",
                headers=self.h(uid), json={"team_id": t2["id"]})
            self.assertEqual(r.status_code, 200, r.text)
        r = self.client.post(
            f"/api/altshot-tee-times/{tt['id']}/teams/{t1['id']}/score",
            headers=self.h(uids[0]), json={"holes": holes})
        self.assertEqual(r.status_code, 200, r.text)
        return tt

    def _summary(self, **params):
        r = self.client.get("/api/altshot-records/summary",
                            params=params, headers=self.h("1"))
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def _par_total(self, course):
        r = self.client.get("/api/courses", headers=self.h("1"))
        self.assertEqual(r.status_code, 200, r.text)
        for c in r.json():
            if c["name"] == course:
                return c["par_total"]
        self.fail(f"course {course} not found")

    def test_summary_empty_when_no_records(self):
        body = self._summary(team_size=2)
        self.assertEqual(body["records"], {})
        self.assertEqual(body["team_size"], 2)
        self.assertEqual(body["tee_position"], "back")

    def test_summary_best_per_course(self):
        self._fill_and_score("Pebble Beach Golf Links", 2, [4] * 18,
                             ["1", "2", "3", "4"])
        self._fill_and_score("TPC Sawgrass", 2, [5] * 18,
                             ["5", "6", "7", "8"])
        recs = self._summary(team_size=2)["records"]
        self.assertEqual(set(recs), {"Pebble Beach Golf Links",
                                     "TPC Sawgrass"})
        peb = recs["Pebble Beach Golf Links"]
        self.assertEqual(peb["total"], 72)
        self.assertEqual(peb["team_display"], "User1 & User2")
        self.assertNotIn("team_name", peb)
        self.assertEqual(peb["to_par"], 72 - self._par_total(
            "Pebble Beach Golf Links"))
        saw = recs["TPC Sawgrass"]
        self.assertEqual(saw["total"], 90)
        self.assertEqual(saw["team_display"], "User5 & User6")
        self.assertEqual(saw["to_par"], 90 - self._par_total(
            "TPC Sawgrass"))

    def test_summary_takes_lowest_total(self):
        self._fill_and_score("Pebble Beach Golf Links", 2, [5] * 18,
                             ["1", "2", "3", "4"])
        self._fill_and_score("Pebble Beach Golf Links", 2, [4] * 18,
                             ["5", "6", "7", "8"])
        recs = self._summary(team_size=2)["records"]
        self.assertEqual(recs["Pebble Beach Golf Links"]["total"], 72)
        self.assertEqual(recs["Pebble Beach Golf Links"]["team_display"],
                         "User5 & User6")

    def test_summary_filters_by_team_size_and_setup(self):
        self._fill_and_score("Pebble Beach Golf Links", 2, [4] * 18,
                             ["1", "2", "3", "4"])
        self._fill_and_score("Pebble Beach Golf Links", 3,
                             [4] * 17 + [8],  # 76
                             ["11", "12", "13", "14", "15", "16"])
        self._fill_and_score("Pebble Beach Golf Links", 2, [3] * 18,
                             ["21", "22", "23", "24"],
                             tee_position="front")
        recs = self._summary(team_size=2)["records"]
        self.assertEqual(recs["Pebble Beach Golf Links"]["total"], 72)
        recs3 = self._summary(team_size=3)["records"]
        self.assertEqual(recs3["Pebble Beach Golf Links"]["total"], 76)
        recs_front = self._summary(team_size=2,
                                   tee_position="front")["records"]
        self.assertEqual(recs_front["Pebble Beach Golf Links"]["total"], 54)

    def test_summary_validation_422(self):
        r = self.client.get("/api/altshot-records/summary",
                            params={"team_size": 5}, headers=self.h("1"))
        self.assertEqual(r.status_code, 422, r.text)
        r = self.client.get(
            "/api/altshot-records/summary",
            params={"team_size": 2, "tee_position": "bogus"},
            headers=self.h("1"))
        self.assertEqual(r.status_code, 422, r.text)


class AppStorePrepApiTestCase(ApiTestCase):
    """Privacy page, account deletion, and crew role management."""

    # -- helpers ------------------------------------------------------
    def _seed_deletion_rows(self, uid):
        """Seed one row per user-subject table for uid; return parent ids."""
        team_id = run(db.create_team(self.db_path, self.t_open,
                                     f"Del Team {uid}", "someone"))
        tt_id = run(db.create_tee_time(self.db_path, self.t_open, "Del TT",
                                       "2026-10-05T10:00:00+00:00", 4,
                                       "someone", "chan"))
        season_id = run(db.create_season(self.db_path, GUILD, "Del Season",
                                         "someone"))

        async def ex(sql, params):
            return await db._execute(self.db_path, sql, params)

        run(ex("INSERT INTO players (discord_id, display_name) VALUES (?,?)",
               (uid, f"User{uid}")))
        run(ex("INSERT INTO devices (discord_id, push_token, platform,"
               " updated_at) VALUES (?,?,?,?)",
               (uid, f"tok-{uid}", "ios", "2026-10-02T00:00:00")))
        run(ex("INSERT INTO notification_prefs (discord_id, updated_at)"
               " VALUES (?,?)", (uid, "2026-10-02T00:00:00")))
        run(ex("INSERT INTO push_outbox (discord_id, title, body, created_at)"
               " VALUES (?,?,?,?)", (uid, "t", "b", "2026-10-02T00:00:00")))
        run(ex("INSERT INTO registrations (tournament_id, player_discord_id,"
               " registered_at) VALUES (?,?,?)",
               (self.t_open, uid, "2026-10-02T00:00:00")))
        run(ex("INSERT INTO team_members (team_id, player_discord_id)"
               " VALUES (?,?)", (team_id, uid)))
        run(ex("INSERT INTO tee_time_players (tee_time_id, player_discord_id)"
               " VALUES (?,?)", (tt_id, uid)))
        run(ex("INSERT INTO join_requests (tee_time_id, player_discord_id,"
               " created_at) VALUES (?,?,?)",
               (tt_id, uid, "2026-10-02T00:00:00")))
        run(ex("INSERT INTO scorecards (tournament_id, round_number,"
               " player_discord_id, holes_json, total, submitted_at)"
               " VALUES (?,?,?,?,?,?)",
               (self.t_open, 1, uid, "[4,4,4]", 12,
                "2026-10-02T00:00:00")))
        run(ex("INSERT INTO season_points (season_id, tournament_id,"
               " player_discord_id, position, points) VALUES (?,?,?,?,?)",
               (season_id, self.t_open, uid, 1, 10)))
        run(ex("INSERT INTO casual_tee_times (id, creator_discord_id, label,"
               " course, pars, starts_at, created_at) VALUES (?,?,?,?,?,?,?)",
               (f"cas-{uid}", "someone", "Cas", "Augusta", PARS_18,
                "2026-10-05T10:00:00+00:00", "2026-10-02T00:00:00")))
        run(ex("INSERT INTO casual_tee_time_players (tee_time_id, discord_id,"
               " joined_at) VALUES (?,?,?)",
               (f"cas-{uid}", uid, "2026-10-02T00:00:00")))
        run(ex("INSERT INTO altshot_teams (id, tee_time_id,"
               " player1_discord_id, created_at) VALUES (?,?,?,?)",
               (f"alt-{uid}", "some-tt", uid, "2026-10-02T00:00:00")))
        run(ex("INSERT INTO altshot_team_members (id, team_id, discord_id,"
               " name, created_at) VALUES (?,?,?,?,?)",
               (f"altm-{uid}", f"alt-{uid}", uid, f"User{uid}",
                "2026-10-02T00:00:00")))
        run(ex("INSERT INTO matchplay_side_members (id, side_id, discord_id,"
               " created_at) VALUES (?,?,?,?)",
               (f"mpm-{uid}", "side-1", uid, "2026-10-02T00:00:00")))
        run(ex("INSERT INTO matchplay_records (id, format, discord_id,"
               " player_name) VALUES (?,?,?,?)",
               (f"mpr-{uid}", "single", uid, f"User{uid}")))

    def _count(self, table, col, uid):
        rows = run(db._fetchall(
            self.db_path,
            f"SELECT COUNT(*) AS n FROM {table} WHERE {col} = ?", (uid,)))
        return rows[0]["n"]

    # -- privacy ------------------------------------------------------
    def test_privacy_public_no_auth(self):
        r = self.client.get("/privacy")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("text/html", r.headers["content-type"])
        self.assertIn("Alpenglow VGC Privacy Policy", r.text)
        self.assertIn("2026-10-02", r.text)
        self.assertIn("Delete Account", r.text)

    # -- support page -------------------------------------------------
    def test_support_public_no_auth(self):
        r = self.client.get("/support")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("text/html", r.headers["content-type"])
        self.assertIn("Alpenglow VGC", r.text)
        self.assertIn("Alpenglowvgc@outlook.com", r.text)
        self.assertIn("Delete Account", r.text)

    # -- account deletion ---------------------------------------------
    def test_delete_me_unauthorized(self):
        r = self.client.delete("/api/players/me")
        self.assertEqual(r.status_code, 401)

    def test_delete_me_cascades(self):
        self._seed_deletion_rows("123")
        self._seed_deletion_rows("999")
        r = self.client.delete("/api/players/me", headers=self.h("123"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json(), {"deleted": True, "discord_id": "123"})

        subject_tables = [
            ("players", "discord_id"), ("devices", "discord_id"),
            ("notification_prefs", "discord_id"), ("push_outbox", "discord_id"),
            ("registrations", "player_discord_id"),
            ("team_members", "player_discord_id"),
            ("tee_time_players", "player_discord_id"),
            ("join_requests", "player_discord_id"),
            ("scorecards", "player_discord_id"),
            ("season_points", "player_discord_id"),
            ("casual_tee_time_players", "discord_id"),
            ("altshot_team_members", "discord_id"),
            ("matchplay_side_members", "discord_id"),
            ("matchplay_records", "discord_id"),
        ]
        for table, col in subject_tables:
            self.assertEqual(self._count(table, col, "123"), 0,
                             f"{table} still has rows for deleted user")
            self.assertGreater(self._count(table, col, "999"), 0,
                               f"{table} lost the OTHER user's rows")

        # Shared altshot team row survives with player1_discord_id nulled.
        row = run(db._fetchone(
            self.db_path,
            "SELECT player1_discord_id FROM altshot_teams WHERE id = ?",
            ("alt-123",)))
        self.assertIsNotNone(row)
        self.assertIsNone(row["player1_discord_id"])
        # Shared history untouched: tournament and season still exist.
        self.assertIsNotNone(run(db.get_tournament(self.db_path,
                                                   self.t_open)))
        self.assertIsNotNone(run(db.get_season(self.db_path, 1)))

    def test_delete_me_unknown_user_ok(self):
        # Auth'd but no player row: still 200 (idempotent).
        r = self.client.get("/api/players/me", headers=self.h("777"))
        self.assertEqual(r.status_code, 200)
        run(db._execute(self.db_path,
                        "DELETE FROM players WHERE discord_id = ?", ("777",)))
        r = self.client.delete("/api/players/me", headers=self.h("777"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["deleted"], True)

    # -- admin player list --------------------------------------------
    def _discord(self, roles, members):
        async def fake_roles():
            return roles

        async def fake_members():
            return members

        main.discord_guild_roles = fake_roles
        main.discord_guild_members = fake_members

    def _ok_roles_members(self):
        self._discord(
            [{"id": "r-mod", "name": "Mod"},
             {"id": "r-td", "name": "Tournament Director"},
             {"id": "r-admin", "name": "Admin"},
             {"id": "r-fun", "name": "Fun"}],
            [{"user": {"id": "123"}, "roles": ["r-mod", "r-admin", "r-fun"]},
             {"user": {"id": "456"}, "roles": ["r-td"]},
             {"user": {"id": "789"}, "roles": []}],
        )

    def test_admin_players_list(self):
        self._admin(True)
        self._ok_roles_members()
        run(db.upsert_player(self.db_path, "123", "Zed"))
        run(db.upsert_player(self.db_path, "456", "Amy"))
        run(db.upsert_player(self.db_path, "789", "NoRole"))
        run(db.set_golfplus_handle(self.db_path, "123", "zedvr"))
        r = self.client.get("/api/admin/players", headers=self.h("1"))
        self.assertEqual(r.status_code, 200, r.text)
        players = r.json()["players"]
        # Ordered by display_name; only crew roles surfaced, sorted.
        # (The authed caller "1" is upserted as User1 by the auth flow.)
        by_name = {p["display_name"]: p for p in players}
        names = sorted(by_name)
        self.assertEqual(names, ["Amy", "NoRole", "User1", "Zed"])
        ordered = [p["display_name"] for p in players]
        self.assertEqual(ordered, sorted(ordered))
        self.assertEqual(by_name["Amy"]["roles"], ["Tournament Director"])
        self.assertEqual(by_name["NoRole"]["roles"], [])
        self.assertEqual(by_name["Zed"]["roles"], ["Admin", "Mod"])
        self.assertEqual(by_name["Zed"]["discord_id"], "123")
        self.assertEqual(by_name["Zed"]["golfplus_handle"], "zedvr")

    def test_admin_players_forbidden_non_admin(self):
        self._admin(False)
        self._ok_roles_members()
        r = self.client.get("/api/admin/players", headers=self.h("1"))
        self.assertEqual(r.status_code, 403)

    def test_admin_players_503_when_discord_down(self):
        self._admin(True)
        # Defaults: discord helpers return None -> 503.
        r = self.client.get("/api/admin/players", headers=self.h("1"))
        self.assertEqual(r.status_code, 503)

    # -- crew role grant/revoke ---------------------------------------
    def _modify(self, result):
        calls = []

        async def fake(discord_id, role_id, action):
            calls.append({"discord_id": discord_id, "role_id": role_id,
                          "action": action})
            return result

        main.discord_modify_member_role = fake
        return calls

    def test_crew_role_grant(self):
        self._admin(True)
        self._ok_roles_members()
        run(db.upsert_player(self.db_path, "123", "Zed"))
        calls = self._modify(204)
        r = self.client.post("/api/admin/crew/roles", headers=self.h("1"),
                             json={"discord_id": "123", "role": "Mod",
                                   "action": "grant"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json(), {"ok": True, "discord_id": "123",
                                    "role": "Mod", "action": "grant"})
        # PUT used for grant, with the Mod role id resolved from guild roles.
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["role_id"], "r-mod")

    def test_crew_role_revoke(self):
        self._admin(True)
        self._ok_roles_members()
        run(db.upsert_player(self.db_path, "123", "Zed"))
        calls = self._modify(204)
        r = self.client.post("/api/admin/crew/roles", headers=self.h("1"),
                             json={"discord_id": "123",
                                   "role": "Tournament Director",
                                   "action": "revoke"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["action"], "revoke")
        self.assertEqual(calls[0]["role_id"], "r-td")

    def test_crew_role_forbidden_non_admin(self):
        self._admin(False)
        self._ok_roles_members()
        run(db.upsert_player(self.db_path, "123", "Zed"))
        self._modify(204)
        r = self.client.post("/api/admin/crew/roles", headers=self.h("1"),
                             json={"discord_id": "123", "role": "Mod",
                                   "action": "grant"})
        self.assertEqual(r.status_code, 403)

    def test_crew_role_422_admin(self):
        self._admin(True)
        self._ok_roles_members()
        run(db.upsert_player(self.db_path, "123", "Zed"))
        r = self.client.post("/api/admin/crew/roles", headers=self.h("1"),
                             json={"discord_id": "123", "role": "Admin",
                                   "action": "grant"})
        self.assertEqual(r.status_code, 422, r.text)
        r = self.client.post("/api/admin/crew/roles", headers=self.h("1"),
                             json={"discord_id": "123",
                                   "role": "Tournament Admin",
                                   "action": "grant"})
        self.assertEqual(r.status_code, 422, r.text)

    def test_crew_role_422_bad_action(self):
        self._admin(True)
        self._ok_roles_members()
        run(db.upsert_player(self.db_path, "123", "Zed"))
        r = self.client.post("/api/admin/crew/roles", headers=self.h("1"),
                             json={"discord_id": "123", "role": "Mod",
                                   "action": "ban"})
        self.assertEqual(r.status_code, 422, r.text)

    def test_crew_role_404_unknown_role_in_guild(self):
        self._admin(True)
        # Guild has no "Mod" role even though it is a manageable name.
        self._discord([{"id": "r-td", "name": "Tournament Director"}], [])
        run(db.upsert_player(self.db_path, "123", "Zed"))
        self._modify(204)
        r = self.client.post("/api/admin/crew/roles", headers=self.h("1"),
                             json={"discord_id": "123", "role": "Mod",
                                   "action": "grant"})
        self.assertEqual(r.status_code, 404, r.text)

    def test_crew_role_404_unregistered_player(self):
        self._admin(True)
        self._ok_roles_members()
        self._modify(204)
        r = self.client.post("/api/admin/crew/roles", headers=self.h("1"),
                             json={"discord_id": "nope", "role": "Mod",
                                   "action": "grant"})
        self.assertEqual(r.status_code, 404, r.text)

    def test_crew_role_404_member_not_in_guild(self):
        self._admin(True)
        self._ok_roles_members()
        run(db.upsert_player(self.db_path, "123", "Zed"))
        self._modify(404)  # Discord: member not in guild
        r = self.client.post("/api/admin/crew/roles", headers=self.h("1"),
                             json={"discord_id": "123", "role": "Mod",
                                   "action": "grant"})
        self.assertEqual(r.status_code, 404, r.text)

    def test_crew_role_502_discord_failure(self):
        self._admin(True)
        self._ok_roles_members()
        run(db.upsert_player(self.db_path, "123", "Zed"))
        self._modify(None)  # transport failure
        r = self.client.post("/api/admin/crew/roles", headers=self.h("1"),
                             json={"discord_id": "123", "role": "Mod",
                                   "action": "grant"})
        self.assertEqual(r.status_code, 502, r.text)
        self._modify(500)  # unexpected Discord status
        r = self.client.post("/api/admin/crew/roles", headers=self.h("1"),
                             json={"discord_id": "123", "role": "Mod",
                                   "action": "grant"})
        self.assertEqual(r.status_code, 502, r.text)

    def test_crew_role_verify_put_vs_delete(self):
        # Directly verify the helper maps grant->PUT, revoke->DELETE.
        self._admin(True)
        self._ok_roles_members()
        run(db.upsert_player(self.db_path, "123", "Zed"))
        calls = self._modify(204)
        self.client.post("/api/admin/crew/roles", headers=self.h("1"),
                         json={"discord_id": "123", "role": "Mod",
                               "action": "grant"})
        self.client.post("/api/admin/crew/roles", headers=self.h("1"),
                         json={"discord_id": "123", "role": "Mod",
                               "action": "revoke"})
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["action"], "grant")
        self.assertEqual(calls[1]["action"], "revoke")


if __name__ == "__main__":
    unittest.main()

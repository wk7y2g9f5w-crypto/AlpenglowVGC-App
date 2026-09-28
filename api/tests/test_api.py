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


def run(coro):
    return asyncio.run(coro)


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        # Fresh DB per test.
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        import os

        os.close(fd)
        run(db.init_db(self.db_path))
        main.DB_PATH = self.db_path
        main.GUILD_ID = GUILD
        main.fetch_discord_user = fake_fetch_discord_user
        main.fetch_crew_status = fake_fetch_crew_status
        main.fetch_admin_status = fake_fetch_admin_status
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
    def _past_tee_time(self, tournament, creator="123", max_players=4):
        """Insert a tee time directly (past start) — the API has no reason to
        create past tee times."""
        tt_id = run(db.create_tee_time(
            self.db_path, tournament, "past flight",
            "2026-01-02T15:30:00+00:00", max_players, creator, None))
        run(db.join_tee_time(self.db_path, tt_id, creator))
        return tt_id

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
        tt_id = self._past_tee_time(self.t_open, creator="123")
        run(db.join_tee_time(self.db_path, tt_id, "456"))
        body = {"player_discord_id": "789", "scores": [4] * 18}
        r = self.client.put(f"/api/tee-times/{tt_id}/scorecard",
                            headers=self.h("123"), json=body)
        self.assertEqual(r.status_code, 403)

    def test_scorecard_submit_happy_and_get(self):
        self.with_tz("123")
        self.with_tz("456")
        tt_id = self._past_tee_time(self.t_open, creator="123")
        run(db.join_tee_time(self.db_path, tt_id, "456"))
        scores = [4] * 18
        body = {"player_discord_id": "123", "scores": scores}
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

    def test_scorecard_solo_pending(self):
        self.with_tz("123")
        tt_id = self._past_tee_time(self.t_open, creator="123")
        body = {"player_discord_id": "123", "scores": [4] * 18}
        r = self.client.put(f"/api/tee-times/{tt_id}/scorecard",
                            headers=self.h("123"), json=body)
        self.assertEqual(r.json()["card"]["status"], "pending")

    def test_scorecard_other_player_in_tee_time(self):
        # The bot lets a playing partner enter someone else's card.
        self.with_tz("123")
        self.with_tz("456")
        tt_id = self._past_tee_time(self.t_open, creator="123")
        run(db.join_tee_time(self.db_path, tt_id, "456"))
        body = {"player_discord_id": "456", "scores": [5] * 18}
        r = self.client.put(f"/api/tee-times/{tt_id}/scorecard",
                            headers=self.h("123"), json=body)
        self.assertEqual(r.status_code, 200, r.text)
        card = r.json()["card"]
        self.assertEqual(card["player_discord_id"], "456")
        self.assertEqual(card["total"], 90)
        self.assertEqual(card["submitted_by"], "123")

    # -- leaderboard --------------------------------------------------
    def test_leaderboard_stroke(self):
        self.with_tz("123")
        self.with_tz("456")
        tt_id = self._past_tee_time(self.t_open, creator="123")
        run(db.join_tee_time(self.db_path, tt_id, "456"))
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
        tt_id = self._past_tee_time(self.t_open, creator="123")
        run(db.join_tee_time(self.db_path, tt_id, "456"))
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
        tt_id = self._past_tee_time(tid, creator="123")
        run(db.join_tee_time(self.db_path, tt_id, "456"))
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
        tt_id = self._past_tee_time(tid, creator="123")
        run(db.join_tee_time(self.db_path, tt_id, "456"))
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
        tt_id = self._past_tee_time(tid, creator="123")
        run(db.join_tee_time(self.db_path, tt_id, "456"))
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


if __name__ == "__main__":
    unittest.main()

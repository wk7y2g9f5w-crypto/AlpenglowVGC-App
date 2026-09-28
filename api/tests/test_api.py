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
        tt_id = self._past_tee_time(self.t_open, creator="123")
        run(db.join_tee_time(self.db_path, tt_id, "456"))
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
        tt_id = self._past_tee_time(self.t_open, creator="123")
        run(db.join_tee_time(self.db_path, tt_id, "456"))
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
        tt_id = self._past_tee_time(self.t_open, creator="123")
        run(db.join_tee_time(self.db_path, tt_id, "456"))
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
        tt_id = self._past_tee_time(self.t_open, creator="123")
        run(db.join_tee_time(self.db_path, tt_id, "456"))
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
        tt_id = self._past_tee_time(self.t_open, creator="123")
        run(db.join_tee_time(self.db_path, tt_id, "456"))
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
        tt_id = self._past_tee_time(self.t_open, creator="123")
        run(db.join_tee_time(self.db_path, tt_id, "456"))
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
        body = {"player_discord_id": "123", "scores": [4] * 18,
                "round_number": 1}
        r = self.client.put(f"/api/tee-times/{tt1}/scorecard",
                            headers=self.h("123"), json=body)
        self.assertEqual(r.status_code, 200, r.text)
        # Leave the first tee time and join the second: allowed now...
        r = self.client.post(f"/api/tee-times/{tt1}/leave",
                             headers=self.h("123"))
        self.assertEqual(r.status_code, 200, r.text)
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
        body = {"player_discord_id": "123", "scores": [4] * 18,
                "round_number": 2}
        r = self.client.put(f"/api/tee-times/{tt2}/scorecard",
                            headers=self.h("123"), json=body)
        self.assertEqual(r.status_code, 200, r.text)

    def test_scorecard_witness_round_trip(self):
        self.with_tz("123")
        self.with_tz("456")
        tt_id = self._past_tee_time(self.t_open, creator="123")
        run(db.join_tee_time(self.db_path, tt_id, "456"))
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

    def test_create_and_list(self):
        tt = self._create_tt()
        self.assertEqual(len(tt["teams"]), 1)  # creator team auto-created
        self.assertEqual(tt["teams"][0]["player1_discord_id"], "1")
        self.assertEqual(tt["teams"][0]["team_size"], 1)
        r = self.client.get("/api/altshot-tee-times", headers=self.h("1"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(len(r.json()["tee_times"]), 1)

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

    def test_join_full_409(self):
        tt = self._create_tt()
        r = self.client.post(f"/api/altshot-tee-times/{tt['id']}/join",
                             headers=self.h("2"),
                             json={"player2_name": "Gus"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(len(r.json()["teams"]), 2)
        r = self.client.post(f"/api/altshot-tee-times/{tt['id']}/join",
                             headers=self.h("3"), json={})
        self.assertEqual(r.status_code, 409, r.text)

    def test_join_idempotent(self):
        tt = self._create_tt()
        r = self.client.post(f"/api/altshot-tee-times/{tt['id']}/join",
                             headers=self.h("2"), json={})
        self.assertEqual(r.status_code, 200, r.text)
        r = self.client.post(f"/api/altshot-tee-times/{tt['id']}/join",
                             headers=self.h("2"), json={})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(len(r.json()["teams"]), 2)

    def _team_url(self, tt_id, team_id, suffix=""):
        return (f"/api/altshot-tee-times/{tt_id}/teams/{team_id}" + suffix)

    def test_update_team_names_and_display(self):
        tt = self._create_tt()
        team = tt["teams"][0]
        r = self.client.patch(self._team_url(tt["id"], team["id"]),
                              headers=self.h("1"),
                              json={"team_name": "The Eagles",
                                    "player2_name": "Dave",
                                    "player3_name": "Erin"})
        self.assertEqual(r.status_code, 200, r.text)
        t = r.json()
        self.assertEqual(t["team_name"], "The Eagles")
        self.assertEqual(t["team_size"], 3)
        self.assertEqual(t["display_name"], "The Eagles")
        self.assertEqual(len(t["player_names"]), 3)
        # No team name: players joined with " & ".
        r = self.client.patch(self._team_url(tt["id"], team["id"]),
                              headers=self.h("1"), json={"team_name": ""})
        self.assertEqual(r.json()["display_name"],
                         r.json()["player1_name"] + " & Dave & Erin")

    def test_submit_needs_two_players(self):
        tt = self._create_tt()
        team = tt["teams"][0]
        r = self.client.post(self._team_url(tt["id"], team["id"], "/score"),
                             headers=self.h("1"),
                             json={"holes": [4] * 18})
        self.assertEqual(r.status_code, 422, r.text)
        self.client.patch(self._team_url(tt["id"], team["id"]),
                          headers=self.h("1"),
                          json={"player2_name": "Dave"})
        r = self.client.post(self._team_url(tt["id"], team["id"], "/score"),
                             headers=self.h("1"),
                             json={"holes": [4] * 18})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["total"], 72)

    def test_submit_bad_holes_422(self):
        tt = self._create_tt()
        team = tt["teams"][0]
        self.client.patch(self._team_url(tt["id"], team["id"]),
                          headers=self.h("1"),
                          json={"player2_name": "Dave"})
        r = self.client.post(self._team_url(tt["id"], team["id"], "/score"),
                             headers=self.h("1"),
                             json={"holes": [4] * 9})
        self.assertEqual(r.status_code, 422, r.text)

    def test_team_edit_forbidden_for_stranger(self):
        tt = self._create_tt()
        team = tt["teams"][0]
        r = self.client.patch(self._team_url(tt["id"], team["id"]),
                              headers=self.h("9"),
                              json={"player2_name": "Mallory"})
        self.assertEqual(r.status_code, 403, r.text)
        r = self.client.post(self._team_url(tt["id"], team["id"], "/score"),
                             headers=self.h("9"),
                             json={"holes": [4] * 18})
        self.assertEqual(r.status_code, 403, r.text)

    def test_records_split_by_team_size(self):
        tt = self._create_tt()
        t1 = tt["teams"][0]
        self.client.patch(self._team_url(tt["id"], t1["id"]),
                          headers=self.h("1"),
                          json={"team_name": "Big Squad",
                                "player2_name": "Dave",
                                "player3_name": "Erin",
                                "player4_name": "Finn"})
        r = self.client.post(f"/api/altshot-tee-times/{tt['id']}/join",
                             headers=self.h("2"),
                             json={"player2_name": "Gus"})
        t2 = [t for t in r.json()["teams"]
              if t["player1_discord_id"] == "2"][0]
        self.client.post(self._team_url(tt["id"], t2["id"], "/score"),
                         headers=self.h("2"), json={"holes": [4] * 18})
        self.client.post(self._team_url(tt["id"], t1["id"], "/score"),
                         headers=self.h("1"), json={"holes": [3] * 18})
        # 2-player board: only the duo.
        r = self.client.get("/api/altshot-records",
                            params={"course": "Pebble Beach Golf Links",
                                    "team_size": 2},
                            headers=self.h("1"))
        self.assertEqual(r.status_code, 200, r.text)
        recs = r.json()["records"]
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["total"], 72)
        self.assertEqual(recs[0]["to_par"], 0)
        self.assertEqual(recs[0]["team_size"], 2)
        # 4-player board: only the squad, with all names visible.
        r = self.client.get("/api/altshot-records",
                            params={"course": "Pebble Beach Golf Links",
                                    "team_size": 4},
                            headers=self.h("1"))
        recs = r.json()["records"]
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["total"], 54)
        self.assertEqual(recs[0]["to_par"], -18)
        self.assertEqual(recs[0]["team_display"], "Big Squad")
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

    def test_delete_score_and_leave(self):
        tt = self._create_tt()
        team = tt["teams"][0]
        self.client.patch(self._team_url(tt["id"], team["id"]),
                          headers=self.h("1"),
                          json={"player2_name": "Dave"})
        self.client.post(self._team_url(tt["id"], team["id"], "/score"),
                         headers=self.h("1"), json={"holes": [4] * 18})
        r = self.client.delete(self._team_url(tt["id"], team["id"], "/score"),
                               headers=self.h("1"))
        self.assertEqual(r.status_code, 200, r.text)
        r = self.client.get("/api/altshot-records",
                            params={"course": "Pebble Beach Golf Links",
                                    "team_size": 2},
                            headers=self.h("1"))
        self.assertEqual(r.json()["records"], [])
        r = self.client.post(f"/api/altshot-tee-times/{tt['id']}/leave",
                             headers=self.h("1"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["teams"], [])

    def test_delete_tee_time_forbidden_for_stranger(self):
        tt = self._create_tt()
        r = self.client.delete(f"/api/altshot-tee-times/{tt['id']}",
                               headers=self.h("9"))
        self.assertEqual(r.status_code, 403, r.text)
        r = self.client.get(f"/api/altshot-tee-times/{tt['id']}",
                            headers=self.h("1"))
        self.assertEqual(r.status_code, 200, r.text)

    def test_404s(self):
        r = self.client.get("/api/altshot-tee-times/nope", headers=self.h("1"))
        self.assertEqual(r.status_code, 404, r.text)
        r = self.client.post("/api/altshot-tee-times/nope/join",
                             headers=self.h("1"), json={})
        self.assertEqual(r.status_code, 404, r.text)


if __name__ == "__main__":
    unittest.main()

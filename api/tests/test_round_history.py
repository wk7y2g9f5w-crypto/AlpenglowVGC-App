"""Tests for GET /api/players/{key}/rounds (round history).

Covers: empty history, tournament + casual cards unified, in_progress
excluded, newest-tee-time-first ordering, owner/crew gating, 404s, and the
"me" alias.
"""
import sys
import unittest
from pathlib import Path

from test_api import (
    ApiTestCase,
    PARS_18,
    GUILD,
    fake_fetch_crew_status,
    run,
)

API_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_DIR))
import main  # noqa: E402

BOT_DIR = str(Path.home() / "workspace" / "golf-tournament-bot")
if BOT_DIR not in sys.path:
    sys.path.insert(0, BOT_DIR)
from src import db  # noqa: E402


class RoundHistoryTest(ApiTestCase):
    def _player(self, uid="123"):
        r = self.client.get("/api/players/me", headers=self.h(uid))
        self.assertEqual(r.status_code, 200)
        return r.json()

    def _past_tt(self, starts_at, label="flight"):
        tt_id = run(db.create_tee_time(
            self.db_path, self.t_open, label, starts_at, 4, "123", None))
        run(db.join_tee_time(self.db_path, tt_id, "123"))
        return tt_id

    def _tournament_card(self, uid, tt_id, scores, status="verified",
                         round_number=1):
        return run(db.upsert_scorecard(
            self.db_path, self.t_open, uid, None, tt_id, scores, status,
            submitted_by=uid, round_number=round_number))

    def _casual_card(self, uid, starts_at, scores, status="verified",
                     label="Sunday skins", fmt="stroke"):
        ctt_id = run(db.create_casual_tee_time(
            self.db_path, uid, label, "Pebble Beach Golf Links", PARS_18,
            starts_at=starts_at, format=fmt))
        return run(db.upsert_scorecard(
            self.db_path, None, uid, None, None, scores, status,
            submitted_by=uid, casual_tee_time_id=ctt_id))

    def test_empty_history(self):
        self._player("123")
        r = self.client.get("/api/players/123/rounds", headers=self.h("123"))
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["rounds"], [])
        self.assertEqual(body["player"]["discord_id"], "123")

    def test_me_alias(self):
        self._player("123")
        r = self.client.get("/api/players/me/rounds", headers=self.h("123"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["rounds"], [])

    def test_tournament_card_fields(self):
        self._player("123")
        tt_id = self._past_tt("2026-02-10T14:00:00+00:00")
        self._tournament_card("123", tt_id, [4] * 18)
        r = self.client.get("/api/players/123/rounds", headers=self.h("123"))
        self.assertEqual(r.status_code, 200)
        rounds = r.json()["rounds"]
        self.assertEqual(len(rounds), 1)
        rd = rounds[0]
        self.assertEqual(rd["kind"], "tournament")
        self.assertEqual(rd["tournament_name"], "API Open")
        self.assertEqual(rd["round_number"], 1)
        self.assertEqual(rd["course"], "Pebble Beach Golf Links")
        self.assertEqual(rd["starts_at"], "2026-02-10T14:00:00+00:00")
        self.assertEqual(rd["scores"], [4] * 18)
        self.assertEqual(rd["pars"], [4] * 18)
        self.assertEqual(rd["total"], 72)
        self.assertEqual(rd["to_par"], 0)
        self.assertEqual(rd["status"], "verified")
        self.assertIsNone(rd["label"])
        self.assertIsNone(rd["format"])

    def test_in_progress_excluded_pending_included(self):
        self._player("123")
        tt_id = self._past_tt("2026-02-10T14:00:00+00:00")
        self._tournament_card("123", tt_id, [4] * 9 + [None] * 9,
                              status="in_progress")
        self._tournament_card("123", tt_id, [5] * 18, status="pending")
        r = self.client.get("/api/players/123/rounds", headers=self.h("123"))
        rounds = r.json()["rounds"]
        self.assertEqual(len(rounds), 1)
        self.assertEqual(rounds[0]["status"], "pending")
        self.assertEqual(rounds[0]["total"], 90)
        self.assertEqual(rounds[0]["to_par"], 18)

    def test_casual_card_fields(self):
        self._player("123")
        self._casual_card("123", "2026-03-01T09:00:00+00:00", [4] * 18,
                          label="Sunday skins", fmt="stroke")
        r = self.client.get("/api/players/123/rounds", headers=self.h("123"))
        rounds = r.json()["rounds"]
        self.assertEqual(len(rounds), 1)
        rd = rounds[0]
        self.assertEqual(rd["kind"], "casual")
        self.assertEqual(rd["label"], "Sunday skins")
        self.assertEqual(rd["format"], "stroke")
        self.assertEqual(rd["course"], "Pebble Beach Golf Links")
        self.assertEqual(rd["starts_at"], "2026-03-01T09:00:00+00:00")
        self.assertIsNone(rd["tournament_id"])
        self.assertEqual(rd["total"], 72)

    def test_ordering_newest_tee_time_first(self):
        self._player("123")
        tt_old = self._past_tt("2026-01-05T10:00:00+00:00", label="old")
        tt_new = self._past_tt("2026-04-05T10:00:00+00:00", label="new")
        self._tournament_card("123", tt_old, [4] * 18)
        self._tournament_card("123", tt_new, [4] * 18)
        self._casual_card("123", "2026-02-15T10:00:00+00:00", [4] * 18)
        r = self.client.get("/api/players/123/rounds", headers=self.h("123"))
        rounds = r.json()["rounds"]
        self.assertEqual(len(rounds), 3)
        kinds = [(rd["kind"], rd["starts_at"][:10]) for rd in rounds]
        self.assertEqual(kinds, [
            ("tournament", "2026-04-05"),
            ("casual", "2026-02-15"),
            ("tournament", "2026-01-05"),
        ])

    def test_other_player_forbidden_for_non_crew(self):
        self._player("123")
        self._player("456")
        tt_id = self._past_tt("2026-02-10T14:00:00+00:00")
        self._tournament_card("123", tt_id, [4] * 18)
        r = self.client.get("/api/players/123/rounds", headers=self.h("456"))
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["code"], "not_card_owner")

    def test_other_player_allowed_for_crew(self):
        self._player("123")
        self._player("456")
        tt_id = self._past_tt("2026-02-10T14:00:00+00:00")
        self._tournament_card("123", tt_id, [4] * 18)

        async def fake_crew(discord_id):
            return True

        main.fetch_crew_status = fake_crew
        try:
            r = self.client.get("/api/players/123/rounds",
                                headers=self.h("456"))
        finally:
            main.fetch_crew_status = fake_fetch_crew_status
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()["rounds"]), 1)

    def test_unknown_player_404(self):
        self._player("123")
        r = self.client.get("/api/players/999/rounds", headers=self.h("123"))
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["code"], "player_not_found")

    def test_team_cards_excluded(self):
        self._player("123")
        tt_id = self._past_tt("2026-02-10T14:00:00+00:00")
        run(db.upsert_scorecard(
            self.db_path, self.t_open, "123", 7, tt_id, [4] * 18,
            "verified", submitted_by="123"))
        r = self.client.get("/api/players/123/rounds", headers=self.h("123"))
        self.assertEqual(r.json()["rounds"], [])


del ApiTestCase

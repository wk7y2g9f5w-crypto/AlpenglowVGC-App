"""Tests for GET /api/tournaments/{id}/players (tournament players list).

Covers: only accounts with a Golf+ username are listed, registered flag
is correct per tournament, registered-first then alphabetical ordering,
404 for unknown tournaments, and auth requirement.
"""
import sys
import unittest
from pathlib import Path

from test_api import ApiTestCase, run

API_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_DIR))
import main  # noqa: E402

BOT_DIR = str(Path.home() / "workspace" / "golf-tournament-bot")
if BOT_DIR not in sys.path:
    sys.path.insert(0, BOT_DIR)
from src import db  # noqa: E402


class TournamentPlayersTest(ApiTestCase):
    def _mk(self, uid, handle=None):
        r = self.client.get("/api/players/me", headers=self.h(uid))
        self.assertEqual(r.status_code, 200)
        if handle is not None:
            run(db.set_golfplus_handle(self.db_path, uid, handle))

    def _get(self, tid, uid="123"):
        return self.client.get(
            f"/api/tournaments/{tid}/players", headers=self.h(uid))

    def test_only_handles_listed_and_registered_flagged(self):
        self._mk("123", "Alumec")
        self._mk("456", "ZedVR")
        self._mk("789")  # no Golf+ username -> excluded
        run(db.register_player(self.db_path, self.t_open, "123"))

        r = self._get(self.t_open)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["tournament_id"], self.t_open)
        players = body["players"]
        self.assertEqual(len(players), 2)
        # Registered first.
        self.assertEqual(players[0]["golfplus_handle"], "Alumec")
        self.assertTrue(players[0]["registered"])
        self.assertIsNotNone(players[0]["registered_at"])
        self.assertEqual(players[1]["golfplus_handle"], "ZedVR")
        self.assertFalse(players[1]["registered"])
        self.assertIsNone(players[1]["registered_at"])
        uids = [p["discord_id"] for p in players]
        self.assertNotIn("789", uids)

    def test_registered_first_then_alphabetical(self):
        self._mk("123", "zedvr")
        self._mk("456", "Alfa")
        self._mk("789", "mike")
        run(db.register_player(self.db_path, self.t_open, "123"))
        run(db.register_player(self.db_path, self.t_open, "456"))

        players = self._get(self.t_open).json()["players"]
        self.assertEqual(
            [p["golfplus_handle"] for p in players],
            ["Alfa", "zedvr", "mike"],
        )
        self.assertEqual(
            [p["registered"] for p in players], [True, True, False])

    def test_flag_is_per_tournament(self):
        self._mk("123", "Alumec")
        run(db.register_player(self.db_path, self.t_inprog, "123"))

        open_players = self._get(self.t_open).json()["players"]
        self.assertFalse(open_players[0]["registered"])
        live_players = self._get(self.t_inprog).json()["players"]
        self.assertTrue(live_players[0]["registered"])

    def test_unknown_tournament_404(self):
        self._mk("123", "Alumec")
        r = self._get(999999)
        self.assertEqual(r.status_code, 404)

    def test_requires_auth(self):
        r = self.client.get(f"/api/tournaments/{self.t_open}/players")
        self.assertEqual(r.status_code, 401)


if __name__ == "__main__":
    unittest.main()

del ApiTestCase

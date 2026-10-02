"""Unit tests for Golf+ handle enrichment in API responses.

Cayden's display rule: the app shows ONLY the Golf+ username (never real
names) wherever the API provides it. These tests assert that every
player-bearing response carries `golfplus_handle` (or a structured
`members`/`players` array with handles) so the app can apply the rule.

- Uses a FRESH temporary SQLite DB (db.init_db) — never the live bot file.
- Monkeypatches Discord helpers: no network, no Discord calls.
- unittest only; run with: python -m pytest tests/ -q
"""
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR))

from test_api import ApiTestCase, run, GUILD  # noqa: E402

from src import db  # noqa: E402


class HandlesApiTestCase(ApiTestCase):
    """golfplus_handle present in every player-bearing API response."""

    def _handle(self, uid, handle):
        # upsert first: set_golfplus_handle is an UPDATE (no-op on missing row).
        run(db.upsert_player(self.db_path, uid, f"User{uid}"))
        run(db.set_golfplus_handle(self.db_path, uid, handle))

    # -- tournament leaderboard --------------------------------------
    def _stroke_board(self):
        self.with_tz("123")
        self.with_tz("456")
        self._handle("123", "alumec")
        tt_id = self._past_tee_time(self.t_open, creator="123")
        run(db.join_tee_time(self.db_path, tt_id, "456"))
        run(db.upsert_scorecard(self.db_path, self.t_open, "123", None, tt_id,
                                [4] * 18, "verified", submitted_by="123"))
        run(db.upsert_scorecard(self.db_path, self.t_open, "456", None, tt_id,
                                [5] * 18, "verified", submitted_by="456"))
        r = self.client.get(f"/api/tournaments/{self.t_open}/leaderboard",
                            headers=self.h("123"))
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["standings"]

    def test_leaderboard_stroke_has_handles(self):
        rows = self._stroke_board()
        by_pid = {x["discord_id"]: x for x in rows}
        self.assertEqual(by_pid["123"]["golfplus_handle"], "alumec")
        self.assertIsNone(by_pid["456"]["golfplus_handle"])
        # display_name still present (Discord untouched, fallback intact).
        self.assertIn("display_name", by_pid["123"])

    def test_leaderboard_match_has_handles(self):
        self._handle("123", "alumec")
        t = run(db.create_tournament(
            self.db_path, GUILD, "Match Night", "match", 9,
            "Pebble Beach Golf Links", "4," * 17 + "4", "m", "1"))
        mid = run(db.create_match(self.db_path, t, "123", "456", "123",
                                  winner="player1"))
        run(db.confirm_match(self.db_path, mid, "player1"))
        r = self.client.get(f"/api/tournaments/{t}/leaderboard",
                            headers=self.h("123"))
        self.assertEqual(r.status_code, 200, r.text)
        rows = {x["discord_id"]: x for x in r.json()["standings"]}
        self.assertEqual(rows["123"]["golfplus_handle"], "alumec")
        self.assertIsNone(rows["456"]["golfplus_handle"])

    # -- casual tee times ---------------------------------------------
    def test_casual_tee_time_players_have_handles(self):
        self.with_tz("123")
        self.with_tz("456")
        self._handle("123", "alumec")
        body = {"label": "Sun casual", "course": "Pebble Beach Golf Links",
                "starts_at": "2030-10-04T14:00:00Z"}
        r = self.client.post("/api/casual-tee-times", headers=self.h("123"),
                             json=body)
        self.assertEqual(r.status_code, 200, r.text)
        tt_id = r.json()["id"]
        r = self.client.post(f"/api/casual-tee-times/{tt_id}/join",
                             headers=self.h("456"))
        self.assertEqual(r.status_code, 200, r.text)
        players = {p["discord_id"]: p for p in r.json()["players"]}
        self.assertEqual(players["123"]["golfplus_handle"], "alumec")
        self.assertIsNone(players["456"]["golfplus_handle"])

    # -- tournament join requests --------------------------------------
    def test_join_requests_have_handles(self):
        self.with_tz("123")
        self.with_tz("456")
        self._handle("456", "tankgolf")
        tt_id = self._past_tee_time(self.t_open, creator="123")
        r = self.client.post(f"/api/tee-times/{tt_id}/request",
                             headers=self.h("456"))
        self.assertEqual(r.status_code, 200, r.text)
        r = self.client.get(f"/api/tee-times/{tt_id}/requests",
                            headers=self.h("123"))
        self.assertEqual(r.status_code, 200, r.text)
        reqs = r.json()
        self.assertEqual(len(reqs), 1)
        self.assertEqual(reqs[0]["golfplus_handle"], "tankgolf")

    # -- matchplay ------------------------------------------------------
    def _matchplay_tt(self):
        body = {"label": "Sat match", "course": "Pebble Beach Golf Links",
                "starts_at": "2030-10-03T14:00:00Z", "format": "single"}
        r = self.client.post("/api/matchplay/tee-times", headers=self.h("1"),
                             json=body)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_matchplay_sides_have_structured_members(self):
        self._handle("1", "alumec")
        self._handle("2", "tankgolf")
        tt = self._matchplay_tt()
        r = self.client.post(f"/api/matchplay/tee-times/{tt['id']}/join",
                             headers=self.h("2"), json={"side_number": 2})
        self.assertEqual(r.status_code, 200, r.text)
        tt = r.json()
        s1, s2 = tt["sides"]
        self.assertEqual(s1["members"][0]["golfplus_handle"], "alumec")
        self.assertEqual(s1["members"][0]["discord_id"], "1")
        self.assertEqual(s2["members"][0]["golfplus_handle"], "tankgolf")
        # Composed labels kept for Discord compatibility.
        self.assertIn("display_name", s1)
        self.assertIn("member_names", s1)

    def test_matchplay_detail_has_structured_members(self):
        self._handle("1", "alumec")
        tt = self._matchplay_tt()
        r = self.client.get(f"/api/matchplay/tee-times/{tt['id']}",
                            headers=self.h("1"))
        self.assertEqual(r.status_code, 200, r.text)
        s1 = r.json()["sides"][0]
        self.assertEqual(s1["members"][0]["golfplus_handle"], "alumec")

    def test_matchplay_records_have_handles(self):
        self._handle("1", "alumec")
        tt = self._matchplay_tt()
        r = self.client.post(f"/api/matchplay/tee-times/{tt['id']}/join",
                             headers=self.h("2"), json={"side_number": 2})
        self.assertEqual(r.status_code, 200, r.text)
        r = self.client.put(f"/api/matchplay/tee-times/{tt['id']}/score",
                            headers=self.h("1"),
                            json={"hole_results": [1, 1, 1] + [0] * 13
                                  + [None, None]})
        self.assertEqual(r.status_code, 200, r.text)
        r = self.client.get("/api/matchplay/records", headers=self.h("1"),
                            params={"format": "single"})
        self.assertEqual(r.status_code, 200, r.text)
        recs = {x["discord_id"]: x for x in r.json()["records"]}
        self.assertEqual(recs["1"]["golfplus_handle"], "alumec")

    # -- altshot ---------------------------------------------------------
    def _altshot_tt(self, uids=("1", "2", "3", "4")):
        body = {"label": "Sat alt-shot", "course": "Pebble Beach Golf Links",
                "starts_at": "2030-10-03T14:00:00Z", "max_teams": 2,
                "team_size": 2, "green_speed": "pro"}
        r = self.client.post("/api/altshot-tee-times", headers=self.h(uids[0]),
                             json=body)
        self.assertEqual(r.status_code, 200, r.text)
        tt = r.json()
        teams = {t["team_number"]: t for t in tt["teams"]}
        # uids[0] (creator) is already on team 1; fill the rest.
        for uid in uids[1:2]:
            r = self.client.post(
                f"/api/altshot-tee-times/{tt['id']}/join",
                headers=self.h(uid), json={"team_id": teams[1]["id"]})
            self.assertEqual(r.status_code, 200, r.text)
        for uid in uids[2:]:
            r = self.client.post(
                f"/api/altshot-tee-times/{tt['id']}/join",
                headers=self.h(uid), json={"team_id": teams[2]["id"]})
            self.assertEqual(r.status_code, 200, r.text)
        r = self.client.get(f"/api/altshot-tee-times/{tt['id']}",
                            headers=self.h(uids[0]))
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_altshot_teams_have_structured_members(self):
        self._handle("1", "alumec")
        self._handle("3", "tankgolf")
        tt = self._altshot_tt()
        teams = {t["team_number"]: t for t in tt["teams"]}
        m1 = {m["discord_id"]: m for m in teams[1]["members"]}
        self.assertEqual(m1["1"]["golfplus_handle"], "alumec")
        m2 = {m["discord_id"]: m for m in teams[2]["members"]}
        self.assertEqual(m2["3"]["golfplus_handle"], "tankgolf")
        # Composed fields kept for Discord compatibility.
        self.assertIn("player_names", teams[1])
        self.assertIn("display_name", teams[1])

    def test_altshot_records_have_player_handles(self):
        self._handle("1", "alumec")
        self._handle("2", "kristian")
        tt = self._altshot_tt(uids=("1", "2", "3", "4"))
        teams = {t["team_number"]: t for t in tt["teams"]}
        r = self.client.post(
            f"/api/altshot-tee-times/{tt['id']}/teams/{teams[1]['id']}/score",
            headers=self.h("1"), json={"holes": [4] * 18})
        self.assertEqual(r.status_code, 200, r.text)
        r = self.client.get(
            "/api/altshot-records", headers=self.h("1"),
            params={"course": "Pebble Beach Golf Links", "team_size": 2,
                    "tee_position": "middle", "pin_position": "white",
                    "wind_strength": "moderate", "green_speed": "pro"})
        self.assertEqual(r.status_code, 200, r.text)
        records = r.json()["records"]
        self.assertTrue(records)
        handles = {p["golfplus_handle"] for p in records[0]["players"]}
        self.assertIn("alumec", handles)
        self.assertIn("kristian", handles)

    def test_altshot_records_summary_has_player_handles(self):
        self._handle("1", "alumec")
        self._handle("2", "kristian")
        tt = self._altshot_tt(uids=("1", "2", "3", "4"))
        teams = {t["team_number"]: t for t in tt["teams"]}
        r = self.client.post(
            f"/api/altshot-tee-times/{tt['id']}/teams/{teams[1]['id']}/score",
            headers=self.h("1"), json={"holes": [4] * 18})
        self.assertEqual(r.status_code, 200, r.text)
        r = self.client.get(
            "/api/altshot-records/summary", headers=self.h("1"),
            params={"team_size": 2, "tee_position": "middle",
                    "pin_position": "white", "wind_strength": "moderate",
                    "green_speed": "pro"})
        self.assertEqual(r.status_code, 200, r.text)
        rec = r.json()["records"]["Pebble Beach Golf Links"]
        handles = {p["golfplus_handle"] for p in rec["players"]}
        self.assertIn("alumec", handles)
        self.assertIn("kristian", handles)


# Unbind the imported base class so pytest does not collect ApiTestCase's whole
# suite a second time under this module. Each duplicate suite burns another
# ~50MB of temp DBs in /tmp's 512MB tmpfs and breaks full runs with
# "database or disk is full".
del ApiTestCase

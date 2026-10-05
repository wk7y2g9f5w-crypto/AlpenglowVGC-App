"""API tests for casual-round formats + scorecards.

- Casual create with each format (stroke, best_ball, match_play, alt_shot),
  including linked match-play / alt-shot game creation.
- Linked games are filtered out of the dedicated Matchplay / AltShot lists.
- Casual scorecard submit/list/get (live + final), gates, and the casual
  leaderboard (stroke totals + best-ball per-hole math).
- Delete cascades from a casual round to its linked game.
- Casual stroke/best-ball cards feed stats.

Uses the ApiTestCase harness from test_api.py (fresh temp DB per test).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_api import ApiTestCase, run  # noqa: E402
from src import db  # noqa: E402

COURSE = "Pebble Beach Golf Links"
STARTS_AT = "2030-10-04T14:00:00Z"


class CasualFormatsTest(ApiTestCase):
    # -- helpers ------------------------------------------------------
    def _mk_casual(self, uid, body):
        body = dict(body, course=COURSE, starts_at=STARTS_AT)
        r = self.client.post("/api/casual-tee-times", headers=self.h(uid),
                             json=body)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def _scores(self, val):
        return [val] * 18

    # -- format creation ----------------------------------------------
    def test_create_stroke_and_best_ball(self):
        for fmt in ("stroke", "best_ball"):
            tt = self._mk_casual("123", {"label": f"Sun {fmt}", "format": fmt})
            self.assertEqual(tt["format"], fmt)
            self.assertIsNone(tt["matchplay_tee_time_id"])
            self.assertIsNone(tt["altshot_tee_time_id"])

    def test_create_match_play_single(self):
        tt = self._mk_casual("123", {
            "label": "Sun MP", "format": "match_play",
            "matchplay": {"format": "single", "team_size": 1},
        })
        self.assertEqual(tt["format"], "match_play")
        mp_id = tt["matchplay_tee_time_id"]
        self.assertIsNotNone(mp_id)
        game = run(db.get_matchplay_tee_time(self.db_path, mp_id))
        self.assertIsNotNone(game)
        self.assertEqual(game["format"], "single")
        # Back-reference set; game lives in the Casual tab, not the
        # dedicated Match Play list.
        self.assertEqual(game["casual_tee_time_id"], tt["id"])
        r = self.client.get("/api/matchplay/tee-times", headers=self.h("123"))
        self.assertEqual(r.status_code, 200, r.text)
        ids = [g["id"] for g in r.json()["tee_times"]]
        self.assertNotIn(mp_id, ids)

    def test_create_match_play_bestball(self):
        tt = self._mk_casual("123", {
            "label": "Sun MP BB", "format": "match_play",
            "matchplay": {"format": "bestball", "team_size": 3},
        })
        game = run(db.get_matchplay_tee_time(
            self.db_path, tt["matchplay_tee_time_id"]))
        self.assertEqual(game["format"], "bestball")
        self.assertEqual(game["team_size"], 3)

    def test_create_match_play_defaults(self):
        # No matchplay sub-object -> 1v1 single.
        tt = self._mk_casual("123", {"label": "Sun MP d", "format": "match_play"})
        game = run(db.get_matchplay_tee_time(
            self.db_path, tt["matchplay_tee_time_id"]))
        self.assertEqual(game["format"], "single")

    def test_create_alt_shot(self):
        tt = self._mk_casual("123", {
            "label": "Sun AS", "format": "alt_shot",
            "altshot": {"max_teams": 2, "team_size": 2},
        })
        self.assertEqual(tt["format"], "alt_shot")
        alt_id = tt["altshot_tee_time_id"]
        game = run(db.get_altshot_tee_time(self.db_path, alt_id))
        self.assertIsNotNone(game)
        self.assertEqual(game["casual_tee_time_id"], tt["id"])
        self.assertEqual(game["max_teams"], 2)
        self.assertEqual(game["team_size"], 2)
        # Filtered out of the dedicated Alt-Shot list.
        r = self.client.get("/api/altshot-tee-times", headers=self.h("123"))
        self.assertEqual(r.status_code, 200, r.text)
        ids = [g["id"] for g in r.json()["tee_times"]]
        self.assertNotIn(alt_id, ids)

    def test_create_alt_shot_one_team(self):
        tt = self._mk_casual("123", {
            "label": "Sun AS 1", "format": "alt_shot",
            "altshot": {"max_teams": 1, "team_size": 4},
        })
        game = run(db.get_altshot_tee_time(
            self.db_path, tt["altshot_tee_time_id"]))
        self.assertEqual(game["max_teams"], 1)
        self.assertEqual(game["team_size"], 4)

    def test_create_bad_format_rejected(self):
        r = self.client.post(
            "/api/casual-tee-times", headers=self.h("123"),
            json={"label": "Bad", "course": COURSE, "format": "scramble"})
        self.assertEqual(r.status_code, 422, r.text)

    def test_create_unknown_course_rejected(self):
        r = self.client.post(
            "/api/casual-tee-times", headers=self.h("123"),
            json={"label": "Bad", "course": "Not A Real Course",
                  "format": "match_play"})
        self.assertEqual(r.status_code, 422, r.text)

    # -- dedicated lists still show standalone games -------------------
    def test_standalone_games_still_listed(self):
        r = self.client.post(
            "/api/matchplay/tee-times", headers=self.h("123"),
            json={"label": "Standalone MP", "course": COURSE,
                  "starts_at": STARTS_AT, "format": "single"})
        self.assertEqual(r.status_code, 200, r.text)
        mp_id = r.json()["id"]
        r = self.client.get("/api/matchplay/tee-times", headers=self.h("123"))
        ids = [g["id"] for g in r.json()["tee_times"]]
        self.assertIn(mp_id, ids)

        r = self.client.post(
            "/api/altshot-tee-times", headers=self.h("123"),
            json={"label": "Standalone AS", "course": COURSE,
                  "starts_at": STARTS_AT, "max_teams": 1, "team_size": 2})
        self.assertEqual(r.status_code, 200, r.text)
        alt_id = r.json()["id"]
        r = self.client.get("/api/altshot-tee-times", headers=self.h("123"))
        ids = [g["id"] for g in r.json()["tee_times"]]
        self.assertIn(alt_id, ids)

    # -- casual scorecards ---------------------------------------------
    def _stroke_round(self, uid, fmt="stroke", label="Sat loop"):
        tt = self._mk_casual("123", {"label": label, "format": fmt})
        tt_id = tt["id"]
        for other in (uid, "456"):
            r = self.client.post(f"/api/casual-tee-times/{tt_id}/join",
                                 headers=self.h(other))
            self.assertIn(r.status_code, (200, 400), r.text)
        return tt_id

    def test_scorecard_live_then_final(self):
        tt_id = self._stroke_round("123")
        # Live partial save.
        scores = [4] * 9 + [None] * 9
        r = self.client.put(f"/api/casual-tee-times/{tt_id}/scorecard",
                            headers=self.h("123"),
                            json={"player_discord_id": "123",
                                  "scores": scores, "complete": False})
        self.assertEqual(r.status_code, 200, r.text)
        card = r.json()["card"]
        self.assertEqual(card["status"], "in_progress")
        self.assertEqual(card["total"], 36)
        self.assertEqual(card["thru"], 9)
        # Final submit -> verified (self-attested), witness kept.
        r = self.client.put(f"/api/casual-tee-times/{tt_id}/scorecard",
                            headers=self.h("123"),
                            json={"player_discord_id": "123",
                                  "scores": self._scores(4),
                                  "complete": True,
                                  "witness_name": "Tank"})
        self.assertEqual(r.status_code, 200, r.text)
        card = r.json()["card"]
        self.assertEqual(card["status"], "verified")
        self.assertEqual(card["witness_name"], "Tank")
        self.assertEqual(card["total"], 72)
        # Get + list.
        r = self.client.get(
            f"/api/casual-tee-times/{tt_id}/scorecard",
            headers=self.h("123"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["card"]["total"], 72)
        r = self.client.get(f"/api/casual-tee-times/{tt_id}/scorecards",
                            headers=self.h("123"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(len(r.json()["scorecards"]), 1)
        # Shot tracking works on casual cards (scorecard_id-keyed).
        r = self.client.put(
            f"/api/scorecards/{card['id']}/holes/1/shots",
            headers=self.h("123"),
            json={"shots": [{"x": 0.1, "y": 0.9, "lie": "tee",
                             "result": "fairway"}]})
        self.assertEqual(r.status_code, 200, r.text)
        r = self.client.get(f"/api/scorecards/{card['id']}/holes/1/shots",
                            headers=self.h("123"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(len(r.json()["shots"]), 1)

    def test_scorecard_gates(self):
        tt_id = self._stroke_round("123")
        # Caller not in the round.
        r = self.client.put(f"/api/casual-tee-times/{tt_id}/scorecard",
                            headers=self.h("999"),
                            json={"player_discord_id": "999",
                                  "scores": self._scores(4),
                                  "complete": True})
        self.assertEqual(r.status_code, 403, r.text)
        # Owner not in the round.
        r = self.client.put(f"/api/casual-tee-times/{tt_id}/scorecard",
                            headers=self.h("123"),
                            json={"player_discord_id": "999",
                                  "scores": self._scores(4),
                                  "complete": True})
        self.assertEqual(r.status_code, 403, r.text)
        # Wrong number of holes.
        r = self.client.put(f"/api/casual-tee-times/{tt_id}/scorecard",
                            headers=self.h("123"),
                            json={"player_discord_id": "123",
                                  "scores": [4] * 17, "complete": True})
        self.assertEqual(r.status_code, 422, r.text)
        # Final submit with holes missing.
        r = self.client.put(f"/api/casual-tee-times/{tt_id}/scorecard",
                            headers=self.h("123"),
                            json={"player_discord_id": "123",
                                  "scores": [4] * 17 + [None],
                                  "complete": True})
        self.assertEqual(r.status_code, 422, r.text)

    def test_scorecard_rejected_for_match_play_casual(self):
        tt = self._mk_casual("123", {"label": "Sun MP", "format": "match_play"})
        for ep in ("scorecards", "scorecard", "leaderboard"):
            r = self.client.get(f"/api/casual-tee-times/{tt['id']}/{ep}",
                                headers=self.h("123"))
            self.assertEqual(r.status_code, 422, r.text)
        r = self.client.put(
            f"/api/casual-tee-times/{tt['id']}/scorecard",
            headers=self.h("123"),
            json={"player_discord_id": "123",
                  "scores": self._scores(4), "complete": True})
        self.assertEqual(r.status_code, 422, r.text)

    # -- casual leaderboard --------------------------------------------
    def test_stroke_leaderboard_totals(self):
        tt_id = self._stroke_round("123")
        self.client.put(f"/api/casual-tee-times/{tt_id}/scorecard",
                        headers=self.h("123"),
                        json={"player_discord_id": "123",
                              "scores": self._scores(4), "complete": True})
        self.client.put(f"/api/casual-tee-times/{tt_id}/scorecard",
                        headers=self.h("456"),
                        json={"player_discord_id": "456",
                              "scores": self._scores(5), "complete": True})
        r = self.client.get(f"/api/casual-tee-times/{tt_id}/leaderboard",
                            headers=self.h("123"))
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["format"], "stroke")
        players = body["players"]
        self.assertEqual([p["total"] for p in players], [72, 90])
        self.assertEqual(players[0]["player_discord_id"], "123")

    def test_best_ball_leaderboard_math(self):
        tt_id = self._stroke_round("123", fmt="best_ball", label="Sun BB")
        # Player 123: 4s on odd holes, 6s on even. Player 456: 6s on odd,
        # 4s on even -> best ball per hole is all 4s = 72.
        s1 = [4 if i % 2 == 0 else 6 for i in range(18)]
        s2 = [6 if i % 2 == 0 else 4 for i in range(18)]
        for uid, scores in (("123", s1), ("456", s2)):
            r = self.client.put(
                f"/api/casual-tee-times/{tt_id}/scorecard",
                headers=self.h(uid),
                json={"player_discord_id": uid, "scores": scores,
                      "complete": True})
            self.assertEqual(r.status_code, 200, r.text)
        r = self.client.get(f"/api/casual-tee-times/{tt_id}/leaderboard",
                            headers=self.h("123"))
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["format"], "best_ball")
        bb = body["best_ball"]
        self.assertEqual(bb["holes"], [4] * 18)
        self.assertEqual(bb["total"], 72)

    # -- delete cascade -------------------------------------------------
    def test_delete_casual_cascades_to_linked_game(self):
        tt = self._mk_casual("123", {"label": "Sun MP", "format": "match_play"})
        mp_id = tt["matchplay_tee_time_id"]
        r = self.client.delete(f"/api/casual-tee-times/{tt['id']}",
                               headers=self.h("123"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIsNone(
            run(db.get_casual_tee_time(self.db_path, tt["id"])))
        self.assertIsNone(
            run(db.get_matchplay_tee_time(self.db_path, mp_id)))

    def test_delete_casual_cascades_scorecards(self):
        tt_id = self._stroke_round("123")
        self.client.put(f"/api/casual-tee-times/{tt_id}/scorecard",
                        headers=self.h("123"),
                        json={"player_discord_id": "123",
                              "scores": self._scores(4), "complete": True})
        card_id = run(db.find_casual_scorecard(self.db_path, tt_id, "123"))[
            "id"]
        r = self.client.delete(f"/api/casual-tee-times/{tt_id}",
                               headers=self.h("123"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIsNone(run(db.get_scorecard(self.db_path, card_id)))

    # -- stats feed from casual cards ----------------------------------
    def test_casual_card_feeds_stats(self):
        tt_id = self._stroke_round("123")
        self.client.put(f"/api/casual-tee-times/{tt_id}/scorecard",
                        headers=self.h("123"),
                        json={"player_discord_id": "123",
                              "scores": self._scores(4), "complete": True})
        r = self.client.get("/api/players/me/stats", headers=self.h("123"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["rounds_played"], 1)

    # -- _casual_json carries format ------------------------------------
    def test_casual_json_includes_format(self):
        r = self.client.get("/api/casual-tee-times", headers=self.h("123"))
        self.assertEqual(r.status_code, 200, r.text)
        for tt in r.json()["tee_times"]:
            self.assertIn(tt["format"], ("stroke", "best_ball",
                                        "match_play", "alt_shot"))


    # -- starts_at is required ---------------------------------------
    def test_create_requires_starts_at(self):
        r = self.client.post(
            "/api/casual-tee-times", headers=self.h("123"),
            json={"label": "No date", "course": COURSE, "format": "stroke"})
        self.assertEqual(r.status_code, 422, r.text)

    def test_create_rejects_blank_starts_at(self):
        r = self.client.post(
            "/api/casual-tee-times", headers=self.h("123"),
            json={"label": "Blank date", "course": COURSE,
                  "starts_at": "  ", "format": "stroke"})
        self.assertEqual(r.status_code, 422, r.text)

    def test_update_cannot_clear_starts_at(self):
        tt = self._mk_casual("123", {"label": "Sun stroke",
                                     "format": "stroke"})
        r = self.client.patch(f"/api/casual-tee-times/{tt['id']}",
                              headers=self.h("123"),
                              json={"starts_at": ""})
        self.assertEqual(r.status_code, 422, r.text)


# Unbind the imported base class so pytest does not collect ApiTestCase's whole
# suite a second time under this module. Each duplicate suite burns another
# ~50MB of temp DBs in /tmp's 512MB tmpfs and breaks full runs with
# "database or disk is full".
del ApiTestCase

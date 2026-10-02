"""Unit tests for the match-play API.

- Uses a FRESH temporary SQLite DB (db.init_db) — never the live bot file.
- Monkeypatches main.fetch_discord_user / fetch_crew_status /
  fetch_mod_admin_status: no network, no Discord calls.
- unittest only; run with: python -m pytest tests/ -q
"""
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR))

from test_api import ApiTestCase, run  # noqa: E402

import main  # noqa: E402

from src import db  # noqa: E402


class MatchPlayApiTestCase(ApiTestCase):
    """Match-play: 2-sided tee times (single or best-ball), live hole
    scoring with server-computed outcomes, and per-player W-L-T records."""

    def setUp(self):
        super().setUp()
        pass  # display names come from OAuth (User1..User3)

    def _create_tt(self, uid="1", **over):
        body = {
            "label": "Sat match",
            "course": "Pebble Beach Golf Links",
            "starts_at": "2030-10-03T14:00:00Z",
            "format": "single",
        }
        body.update(over)
        r = self.client.post("/api/matchplay/tee-times", headers=self.h(uid),
                             json=body)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def _join(self, tt_id, uid, side):
        r = self.client.post(f"/api/matchplay/tee-times/{tt_id}/join",
                             headers=self.h(uid),
                             json={"side_number": side})
        return r

    def _full_single(self):
        """Single match with both sides filled (1=User1, 2=User2)."""
        tt = self._create_tt()
        r = self._join(tt["id"], "2", 2)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def _put(self, tt_id, uid, results):
        return self.client.put(f"/api/matchplay/tee-times/{tt_id}/score",
                               headers=self.h(uid),
                               json={"hole_results": results})

    def _records(self, format="single"):
        r = self.client.get("/api/matchplay/records", headers=self.h("1"),
                            params={"format": format})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["records"]

    # -- create -------------------------------------------------------
    def test_create_single_happy(self):
        tt = self._create_tt()
        self.assertEqual(tt["format"], "single")
        self.assertEqual(tt["team_size"], 1)
        self.assertEqual(tt["side_cap"], 1)
        self.assertEqual(len(tt["sides"]), 2)
        s1, s2 = tt["sides"]
        self.assertEqual(s1["side_number"], 1)
        self.assertEqual(s1["member_discord_ids"], ["1"])
        self.assertEqual(s1["display_name"], "User1")
        self.assertEqual(s2["size"], 0)
        self.assertFalse(tt["both_full"])
        self.assertIsNone(tt["score"])

    def test_create_bestball_happy(self):
        tt = self._create_tt(format="bestball", team_size=3)
        self.assertEqual(tt["format"], "bestball")
        self.assertEqual(tt["side_cap"], 3)
        # No team names: sides are identified by their players.
        self.assertNotIn("team_name", tt["sides"][0])
        self.assertNotIn("team_name", tt["sides"][1])
        self.assertEqual(tt["sides"][0]["display_name"], "User1")
        self.assertEqual(tt["sides"][1]["display_name"], "")

    def test_create_ignores_team_name_fields(self):
        # Stale clients may still send side team names; they are ignored,
        # never 422, and never create a registry claim.
        tt = self._create_tt(format="bestball", team_size=2,
                             side1_team_name="Eagles",
                             side2_team_name="Kings")
        self.assertNotIn("team_name", tt["sides"][0])
        self.assertEqual(tt["sides"][0]["display_name"], "User1")
        # No registry row was created for the ignored name.
        row = run(db._team_name_row(self.db_path, "eagles"))
        self.assertIsNone(row)

    def test_create_defaults_setup_back_black(self):
        tt = self._create_tt()
        self.assertEqual(tt["tee_position"], "back")
        self.assertEqual(tt["pin_position"], "black")
        self.assertEqual(tt["wind_strength"], "moderate")
        self.assertEqual(tt["green_speed"], "pro")

    def test_create_bad_format_422(self):
        r = self.client.post(
            "/api/matchplay/tee-times", headers=self.h("1"),
            json={"label": "x", "course": "Pebble Beach Golf Links",
                  "format": "scramble"})
        self.assertEqual(r.status_code, 422, r.text)

    def test_create_bad_team_size_422(self):
        base = {"label": "x", "course": "Pebble Beach Golf Links"}
        for body in (
            {**base, "format": "single", "team_size": 3},
            {**base, "format": "bestball", "team_size": 5},
            {**base, "format": "bestball", "team_size": 1},
            {**base, "format": "bestball"},  # missing -> required
        ):
            r = self.client.post("/api/matchplay/tee-times",
                                 headers=self.h("1"), json=body)
            self.assertEqual(r.status_code, 422, r.text)

    def test_create_bad_setup_422(self):
        r = self.client.post(
            "/api/matchplay/tee-times", headers=self.h("1"),
            json={"label": "x", "course": "Pebble Beach Golf Links",
                  "tee_position": "tips"})
        self.assertEqual(r.status_code, 422, r.text)

    def test_create_bad_course_422(self):
        r = self.client.post(
            "/api/matchplay/tee-times", headers=self.h("1"),
            json={"label": "x", "course": "Fake CC"})
        self.assertEqual(r.status_code, 422, r.text)

    def test_list_and_get(self):
        tt = self._create_tt()
        r = self.client.get("/api/matchplay/tee-times", headers=self.h("1"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(len(r.json()["tee_times"]), 1)
        r = self.client.get(f"/api/matchplay/tee-times/{tt['id']}",
                            headers=self.h("1"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["id"], tt["id"])

    # -- join / leave --------------------------------------------------
    def test_join_side2_ok(self):
        tt = self._create_tt()
        r = self._join(tt["id"], "2", 2)
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body["both_full"])
        self.assertEqual(body["sides"][1]["member_discord_ids"], ["2"])

    def test_join_full_409(self):
        tt = self._create_tt()
        self.assertEqual(self._join(tt["id"], "2", 2).status_code, 200)
        r = self._join(tt["id"], "3", 2)
        self.assertEqual(r.status_code, 409, r.text)

    def test_join_already_in_409(self):
        tt = self._full_single()["id"]
        r = self._join(tt, "2", 1)  # User2 already on side 2
        self.assertEqual(r.status_code, 409, r.text)

    def test_join_bad_side_422(self):
        tt = self._create_tt()
        r = self._join(tt["id"], "2", 3)
        self.assertEqual(r.status_code, 422, r.text)

    def test_join_bestball_cap(self):
        tt = self._create_tt(format="bestball", team_size=2)
        self.assertEqual(self._join(tt["id"], "2", 1).status_code, 200)
        self.assertEqual(self._join(tt["id"], "3", 2).status_code, 200)
        r = self._join(tt["id"], "4", 2)
        self.assertEqual(r.status_code, 200, r.text)
        # Side 2 is now full (2/2); a third player is rejected.
        r = self._join(tt["id"], "5", 2)
        self.assertEqual(r.status_code, 409, r.text)

    def test_leave_frees_spot(self):
        tt = self._full_single()
        r = self.client.post(f"/api/matchplay/tee-times/{tt['id']}/leave",
                             headers=self.h("2"))
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertFalse(body["both_full"])
        self.assertEqual(body["sides"][1]["size"], 0)
        # Someone else can take the freed spot.
        self.assertEqual(self._join(tt["id"], "3", 2).status_code, 200)

    # -- scoring --------------------------------------------------------
    def test_score_gate_sides_not_full_409(self):
        tt = self._create_tt()
        r = self._put(tt["id"], "1", [1] + [None] * 17)
        self.assertEqual(r.status_code, 409, r.text)

    def test_score_live_in_progress(self):
        tt = self._full_single()
        r = self._put(tt["id"], "1", [1] + [None] * 17)
        self.assertEqual(r.status_code, 200, r.text)
        s = r.json()["score"]
        self.assertEqual(s["status"], "in_progress")
        self.assertEqual(s["lead"], 1)
        self.assertEqual(s["played"], 1)
        self.assertEqual(s["remaining"], 17)
        self.assertEqual(s["live_text"], "User1 1 UP")
        # Either side's member may save.
        r = self._put(tt["id"], "2", [1, -1] + [None] * 16)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["score"]["live_text"], "All Square")

    def test_score_dormie(self):
        tt = self._full_single()
        r = self._put(tt["id"], "1", [1, 1] + [0] * 14 + [None, None])
        self.assertEqual(r.status_code, 200, r.text)
        s = r.json()["score"]
        self.assertEqual(s["status"], "in_progress")
        self.assertEqual(s["live_text"], "Dormie")

    def test_score_clinch_3_and_2(self):
        tt = self._full_single()
        r = self._put(tt["id"], "1", [1, 1, 1] + [0] * 13 + [None, None])
        self.assertEqual(r.status_code, 200, r.text)
        s = r.json()["score"]
        self.assertEqual(s["status"], "completed")
        self.assertEqual(s["winner_side"], 1)
        self.assertEqual(s["result_text"], "3&2")
        self.assertEqual(s["live_text"], "User1 wins 3&2")
        recs = self._records()
        self.assertEqual(
            [(x["player_name"], x["wins"], x["losses"], x["ties"])
             for x in recs],
            [("User1", 1, 0, 0), ("User2", 0, 1, 0)])

    def test_score_side2_clinch(self):
        tt = self._full_single()
        r = self._put(tt["id"], "2", [-1] * 10 + [0] * 6 + [None, None])
        s = r.json()["score"]
        self.assertEqual(s["status"], "completed")
        self.assertEqual(s["winner_side"], 2)
        self.assertEqual(s["result_text"], "10&2")
        self.assertEqual(s["live_text"], "User2 wins 10&2")

    def test_score_all_square_tie(self):
        tt = self._full_single()
        r = self._put(tt["id"], "1", [1, -1] * 9)
        self.assertEqual(r.status_code, 200, r.text)
        s = r.json()["score"]
        self.assertEqual(s["status"], "completed")
        self.assertIsNone(s["winner_side"])
        self.assertEqual(s["result_text"], "All Square")
        self.assertEqual(s["live_text"], "All Square")
        recs = self._records()
        self.assertEqual(
            [(x["player_name"], x["wins"], x["losses"], x["ties"])
             for x in recs],
            [("User1", 0, 0, 1), ("User2", 0, 0, 1)])

    def test_score_2_up_finish(self):
        tt = self._full_single()
        r = self._put(tt["id"], "1", [1, 1] + [0] * 16)
        s = r.json()["score"]
        self.assertEqual(s["status"], "completed")
        self.assertEqual(s["winner_side"], 1)
        self.assertEqual(s["result_text"], "2 UP")
        self.assertEqual(s["live_text"], "User1 wins 2 UP")

    def test_score_bad_results_422(self):
        tt = self._full_single()
        for bad in ([1] * 17, [1] * 19, [2] + [None] * 17, "nope"):
            r = self._put(tt["id"], "1", bad)
            self.assertEqual(r.status_code, 422, r.text)

    def test_score_nonmember_403(self):
        tt = self._full_single()
        r = self._put(tt["id"], "9", [1] + [None] * 17)
        self.assertEqual(r.status_code, 403, r.text)

    def test_get_score_null_before_save(self):
        tt = self._full_single()
        r = self.client.get(f"/api/matchplay/tee-times/{tt['id']}/score",
                            headers=self.h("1"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIsNone(r.json()["score"])

    # -- completed-score lock -------------------------------------------
    def _completed(self):
        tt = self._full_single()
        r = self._put(tt["id"], "1", [1, 1, 1] + [0] * 13 + [None, None])
        self.assertEqual(r.json()["score"]["status"], "completed")
        return tt

    def test_completed_edit_refused_member(self):
        tt = self._completed()
        r = self._put(tt["id"], "1", [1] + [None] * 17)
        self.assertEqual(r.status_code, 403, r.text)

    def test_completed_edit_refused_tournament_director(self):
        # A tournament director is not a mod/admin: same 403.
        tt = self._completed()
        r = self._put(tt["id"], "9", [1] + [None] * 17)
        self.assertEqual(r.status_code, 403, r.text)

    def test_completed_edit_allowed_mod_admin_retallies(self):
        tt = self._completed()
        self._mod_admin(True)
        # Flip the result to a side-2 win; records must un-apply the old
        # result and apply the new one.
        r = self._put(tt["id"], "1", [-1, -1, -1] + [0] * 13 + [None, None])
        self.assertEqual(r.status_code, 200, r.text)
        s = r.json()["score"]
        self.assertEqual(s["winner_side"], 2)
        self.assertEqual(s["result_text"], "3&2")
        recs = self._records()
        self.assertEqual(
            [(x["player_name"], x["wins"], x["losses"], x["ties"])
             for x in recs],
            [("User2", 1, 0, 0), ("User1", 0, 1, 0)])

    def test_completed_delete_refused_member(self):
        tt = self._completed()
        r = self.client.delete(f"/api/matchplay/tee-times/{tt['id']}/score",
                               headers=self.h("1"))
        self.assertEqual(r.status_code, 403, r.text)

    def test_completed_delete_allowed_mod_admin_clears_and_untallies(self):
        tt = self._completed()
        self.assertEqual(len(self._records()), 2)
        self._mod_admin(True)
        r = self.client.delete(f"/api/matchplay/tee-times/{tt['id']}/score",
                               headers=self.h("1"))
        self.assertEqual(r.status_code, 200, r.text)
        r = self.client.get(f"/api/matchplay/tee-times/{tt['id']}/score",
                            headers=self.h("1"))
        s = r.json()["score"]
        self.assertEqual(s["status"], "in_progress")
        self.assertEqual(s["hole_results"], [None] * 18)
        recs = self._records()
        self.assertTrue(
            all(x["wins"] == 0 and x["losses"] == 0 for x in recs), recs)

    def test_in_progress_edit_allowed_member(self):
        tt = self._full_single()
        self.assertEqual(
            self._put(tt["id"], "1", [1] + [None] * 17).status_code, 200)
        r = self._put(tt["id"], "1", [1, 1] + [None] * 16)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["score"]["lead"], 2)

    def test_in_progress_delete_allowed_member(self):
        tt = self._full_single()
        self.assertEqual(
            self._put(tt["id"], "1", [1] + [None] * 17).status_code, 200)
        r = self.client.delete(f"/api/matchplay/tee-times/{tt['id']}/score",
                               headers=self.h("2"))
        self.assertEqual(r.status_code, 200, r.text)

    # -- records ----------------------------------------------------------
    def test_records_sorted_wins_first(self):
        # User1 beats User2 twice; User3 beats User2 once.
        t1 = self._full_single()
        self._put(t1["id"], "1", [1, 1, 1] + [0] * 13 + [None, None])
        t2 = self._create_tt()
        self._join(t2["id"], "3", 2)
        self._put(t2["id"], "1", [1, 1, 1] + [0] * 13 + [None, None])
        t3 = self._create_tt(uid="2")
        self._join(t3["id"], "3", 2)
        self._put(t3["id"], "2", [-1, -1, -1] + [0] * 13 + [None, None])
        recs = self._records()
        names = [x["player_name"] for x in recs]
        self.assertEqual(names, ["User1", "User3", "User2"])
        alumec = recs[0]
        self.assertEqual((alumec["wins"], alumec["losses"], alumec["ties"]),
                         (2, 0, 0))
        tank = recs[2]
        self.assertEqual((tank["wins"], tank["losses"]), (0, 2))

    def test_records_keyed_by_format(self):
        # Wins at DIFFERENT courses accumulate in the same format row.
        t1 = self._full_single()
        self._put(t1["id"], "1", [1, 1, 1] + [0] * 13 + [None, None])
        t2 = self._create_tt(course="Pinehurst No. 2")
        self._join(t2["id"], "2", 2)
        self._put(t2["id"], "1", [1, 1, 1] + [0] * 13 + [None, None])
        recs = self._records("single")
        by_name = {x["player_name"]: x for x in recs}
        self.assertEqual(
            (by_name["User1"]["wins"], by_name["User1"]["losses"]),
            (2, 0))
        self.assertEqual(
            (by_name["User2"]["wins"], by_name["User2"]["losses"]),
            (0, 2))
        # A best-ball match writes independent rows: single tallies
        # untouched, bestball rows separate.
        bb = self._create_tt(format="bestball", team_size=2)
        self._join(bb["id"], "2", 1)
        self._join(bb["id"], "3", 2)
        self._join(bb["id"], "4", 2)
        self._put(bb["id"], "1", [1, 1, 1] + [0] * 13 + [None, None])
        bb_recs = self._records("bestball")
        bb_by_name = {x["player_name"]: x for x in bb_recs}
        self.assertEqual(
            (bb_by_name["User1"]["wins"], bb_by_name["User2"]["wins"]),
            (1, 1))
        self.assertEqual(
            (bb_by_name["User3"]["losses"], bb_by_name["User4"]["losses"]),
            (1, 1))
        # Single rows unchanged by the best-ball result.
        recs = self._records("single")
        by_name = {x["player_name"]: x for x in recs}
        self.assertEqual(by_name["User1"]["wins"], 2)

    def test_records_format_required_and_validated(self):
        r = self.client.get("/api/matchplay/records", headers=self.h("1"))
        self.assertEqual(r.status_code, 422, r.text)
        r = self.client.get("/api/matchplay/records", headers=self.h("1"),
                            params={"format": "scramble"})
        self.assertEqual(r.status_code, 422, r.text)
        r = self.client.get("/api/matchplay/records", headers=self.h("1"),
                            params={"format": "bestball"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["format"], "bestball")

    # -- tee time management ----------------------------------------------
    def test_update_tee_time_creator_ok(self):
        tt = self._create_tt()
        r = self.client.patch(f"/api/matchplay/tee-times/{tt['id']}",
                              headers=self.h("1"),
                              json={"label": "Renamed"})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["label"], "Renamed")
        self.assertNotIn("team_name", body["sides"][0])

    def test_update_ignores_team_name_fields(self):
        # Stale clients may still send side team names on update; ignored.
        tt = self._create_tt(format="bestball", team_size=2)
        r = self.client.patch(f"/api/matchplay/tee-times/{tt['id']}",
                              headers=self.h("1"),
                              json={"side1_team_name": "Eagles"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertNotIn("team_name", r.json()["sides"][0])

    def test_update_tee_time_403_not_creator(self):
        tt = self._create_tt()
        r = self.client.patch(f"/api/matchplay/tee-times/{tt['id']}",
                              headers=self.h("2"), json={"label": "Nope"})
        self.assertEqual(r.status_code, 403, r.text)

    def test_delete_tee_time_creator_ok(self):
        tt = self._create_tt()
        r = self.client.delete(f"/api/matchplay/tee-times/{tt['id']}",
                               headers=self.h("1"))
        self.assertEqual(r.status_code, 200, r.text)
        r = self.client.get(f"/api/matchplay/tee-times/{tt['id']}",
                            headers=self.h("1"))
        self.assertEqual(r.status_code, 404, r.text)


# Unbind the imported base class so pytest does not collect ApiTestCase's whole
# suite a second time under this module. Each duplicate suite burns another
# ~50MB of temp DBs in /tmp's 512MB tmpfs and breaks full runs with
# "database or disk is full".
del ApiTestCase

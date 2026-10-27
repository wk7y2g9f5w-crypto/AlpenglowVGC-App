"""Joined-witness rule for solo tournament rounds (Cayden 2026-10-17).

Tee times created under the rule (witness_rule = 1, i.e. every new tee
time) may not play solo:

- A solo tee time (one player / one team) cannot start until a witness
  has joined it — no witness, no start, admins included.
- Witnesses join THEMSELVES via Join as Witness; only an admin can add
  someone else. The creator can never assign a witness, and a player on
  the tee time can't be its witness.
- The solo player's final submit waits at awaiting_witness until a
  joined witness co-submits the card; both submission timestamps are
  recorded (submitted_at for the player, witness_submitted_at + the
  witness id for the co-submit). Crew verify remains the override.
- Grandfathered tee times (witness_rule = 0) are exempt from all of it
  (their named-witness alt-shot path lives in
  test_solo_altshot_witness.py).
- Viewer (2026-10-27) is a reserved stand-in witness ID with no player
  row or login: only crew/admin may add or remove him, only on a solo
  witness-rule tee time; his rounds start normally and their final
  submit skips the co-submit wait (witness_name records "Viewer", and
  no co-submit fields are ever filled in for him).

Fresh temp DB, monkeypatched fetch_* — see test_api.ApiTestCase.
Run with: python -m pytest api/tests/ -q
"""
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR))

from test_api import ApiTestCase, run, GUILD, main  # noqa: E402

from src import db  # noqa: E402

PARS_18 = "4,4,3,4,5,4,3,4,4,4,4,3,4,5,4,3,4,4"


def _code(r):
    body = r.json()
    if isinstance(body, dict):
        if body.get("code"):
            return body["code"]
        detail = body.get("detail")
        if isinstance(detail, dict):
            return detail.get("code")
    return None


class WitnessRuleTestCase(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.tid = None
        for uid, handle in [("1", "Alpha"), ("2", "Bravo"),
                            ("3", "Charlie"), ("4", "Delta")]:
            self.with_tz(uid)
            run(db.set_golfplus_handle(self.db_path, uid, handle))

    # -- helpers ------------------------------------------------------
    def _mk(self, fmt="stroke"):
        self.tid = run(db.create_tournament(
            self.db_path, GUILD, f"Witness {fmt}", fmt, 18,
            "Pebble Beach Golf Links", PARS_18, "witness", "1"))
        for uid in ["1", "2", "3", "4"]:
            r = self.client.post(
                f"/api/tournaments/{self.tid}/register",
                headers=self.h(uid))
            self.assertEqual(r.status_code, 200, r.text)
        return self.tid

    def _tt(self, uid="1", **kw):
        body = {"label": "Sheet", "date": "2026-10-03", "time": "09:30",
                "max_players": 4}
        body.update(kw)
        r = self.client.post(
            f"/api/tournaments/{self.tid}/tee-times", headers=self.h(uid),
            json=body)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def _grandfather(self, tt_id):
        run(db._execute(
            self.db_path,
            "UPDATE tee_times SET witness_rule = 0 WHERE id = ?",
            (tt_id,)))

    def _listed_tt(self, tt_id):
        r = self.client.get(
            f"/api/tournaments/{self.tid}/tee-times", headers=self.h("1"))
        self.assertEqual(r.status_code, 200, r.text)
        return next(t for t in r.json() if t["id"] == tt_id)

    def _start_via_api(self, tt_id, uid="1"):
        return self.client.post(
            f"/api/tee-times/{tt_id}/start", headers=self.h(uid))

    def _join_witness(self, tt_id, uid):
        return self.client.post(
            f"/api/tee-times/{tt_id}/witness", headers=self.h(uid))

    def _submit_stroke(self, tt_id, uid="1", **kw):
        body = {"player_discord_id": uid, "scores": [4] * 18,
                "round_number": 1, "complete": True}
        body.update(kw)
        return self.client.put(
            f"/api/tee-times/{tt_id}/scorecard", headers=self.h(uid),
            json=body)

    # -- witness join / leave ------------------------------------------
    def test_witness_joins_and_is_listed_with_timestamp(self):
        self._mk()
        tt = self._tt()
        self.assertTrue(tt["witness_rule"])
        r = self._join_witness(tt["id"], "3")
        self.assertEqual(r.status_code, 200, r.text)
        listed = self._listed_tt(tt["id"])
        self.assertEqual(len(listed["witnesses"]), 1)
        w = listed["witnesses"][0]
        self.assertEqual(w["discord_id"], "3")
        self.assertIsNotNone(w["joined_at"])
        # Leaving removes them again.
        r = self.client.delete(
            f"/api/tee-times/{tt['id']}/witness", headers=self.h("3"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self._listed_tt(tt["id"])["witnesses"], [])

    def test_player_cannot_witness_own_tee_time(self):
        self._mk()
        tt = self._tt()  # creator "1" auto-joined as the solo player
        r = self._join_witness(tt["id"], "1")
        self.assertEqual(r.status_code, 403, r.text)
        self.assertEqual(_code(r), "is_player")

    def test_creator_cannot_assign_a_witness(self):
        self._mk()
        tt = self._tt()
        # The admin-add endpoint is the only way to add someone else,
        # and a non-admin creator gets refused.
        r = self.client.post(
            f"/api/tee-times/{tt['id']}/witnesses/3", headers=self.h("1"))
        self.assertEqual(r.status_code, 403, r.text)
        self.assertEqual(self._listed_tt(tt["id"])["witnesses"], [])

    def test_admin_can_add_and_remove_witness(self):
        self._mk()
        tt = self._tt()
        self._admin(True)
        r = self.client.post(
            f"/api/tee-times/{tt['id']}/witnesses/3", headers=self.h("1"))
        self.assertEqual(r.status_code, 200, r.text)
        listed = self._listed_tt(tt["id"])
        self.assertEqual([w["discord_id"] for w in listed["witnesses"]],
                         ["3"])
        self.assertEqual(listed["witnesses"][0]["added_by"], "1")
        r = self.client.delete(
            f"/api/tee-times/{tt['id']}/witnesses/3", headers=self.h("1"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self._listed_tt(tt["id"])["witnesses"], [])

    # -- Viewer stand-in (Cayden 2026-10-27) ----------------------------
    def test_regular_player_cannot_add_viewer(self):
        self._mk()
        tt = self._tt()
        self._crew(False)
        self._admin(False)
        r = self.client.post(
            f"/api/tee-times/{tt['id']}/witnesses/viewer",
            headers=self.h("1"))
        self.assertEqual(r.status_code, 403, r.text)
        self.assertEqual(self._listed_tt(tt["id"])["witnesses"], [])

    def test_crew_non_admin_can_add_and_remove_viewer(self):
        self._mk()
        tt = self._tt()
        self._crew(True)
        self._admin(False)
        r = self.client.post(
            f"/api/tee-times/{tt['id']}/witnesses/viewer",
            headers=self.h("2"))
        self.assertEqual(r.status_code, 200, r.text)
        listed = self._listed_tt(tt["id"])
        self.assertEqual(len(listed["witnesses"]), 1)
        w = listed["witnesses"][0]
        self.assertEqual(w["discord_id"], "viewer")
        self.assertEqual(w["display_name"], "Viewer")
        self.assertIsNone(w["golfplus_handle"])
        self.assertTrue(w["is_viewer"])
        self.assertEqual(w["added_by"], "2")
        # Viewer has no player row — he is not a login account.
        self.assertIsNone(run(db.get_player(self.db_path, "viewer")))
        r = self.client.delete(
            f"/api/tee-times/{tt['id']}/witnesses/viewer",
            headers=self.h("2"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self._listed_tt(tt["id"])["witnesses"], [])

    def test_admin_can_add_viewer_without_crew_fake(self):
        self._mk()
        tt = self._tt()
        self._crew(False)
        self._admin(True)
        r = self.client.post(
            f"/api/tee-times/{tt['id']}/witnesses/viewer",
            headers=self.h("1"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(
            [w["discord_id"] for w in self._listed_tt(tt["id"])["witnesses"]],
            ["viewer"])

    def test_viewer_refused_on_grandfathered_or_multi_player(self):
        self._mk()
        tt = self._tt()
        self._crew(True)
        self._grandfather(tt["id"])
        r = self.client.post(
            f"/api/tee-times/{tt['id']}/witnesses/viewer",
            headers=self.h("2"))
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(_code(r), "witness_not_required")

        tt2 = self._tt(uid="3")
        r = self.client.post(
            f"/api/tee-times/{tt2['id']}/join", headers=self.h("2"))
        self.assertEqual(r.status_code, 200, r.text)
        r = self.client.post(
            f"/api/tee-times/{tt2['id']}/witnesses/viewer",
            headers=self.h("4"))
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(_code(r), "viewer_solo_only")

    def test_viewer_starts_round_and_submit_skips_co_submit(self):
        self._mk()
        tt = self._tt()
        self._crew(True)
        r = self.client.post(
            f"/api/tee-times/{tt['id']}/witnesses/viewer",
            headers=self.h("2"))
        self.assertEqual(r.status_code, 200, r.text)
        self._admin(True)
        r = self._start_via_api(tt["id"])
        self.assertEqual(r.status_code, 200, r.text)
        r = self._submit_stroke(tt["id"])
        self.assertEqual(r.status_code, 200, r.text)
        card = r.json()["card"]
        self.assertEqual(card["status"], "pending")
        self.assertFalse(card["awaiting_witness"])
        # Honest record: Viewer witnessed, but nobody co-submitted.
        self.assertEqual(card["witness_name"], "Viewer")
        self.assertIsNone(card["witness_discord_id"])
        self.assertIsNone(card["witness_submitted_at"])
        # And there is no co-submit step waiting to happen.
        r = self.client.post(
            f"/api/scorecards/{card['id']}/witness-submit",
            headers=self.h("3"))
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(_code(r), "not_awaiting_witness")

    def test_witness_join_on_grandfathered_tee_time_refused(self):
        self._mk()
        tt = self._tt()
        self._grandfather(tt["id"])
        r = self._join_witness(tt["id"], "3")
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(_code(r), "witness_not_required")

    # -- the start gate --------------------------------------------------
    def test_solo_start_blocked_without_witness(self):
        self._mk()
        tt = self._tt()
        self._admin(True)  # admins skip creator/timing checks only
        r = self._start_via_api(tt["id"])
        self.assertEqual(r.status_code, 422, r.text)
        self.assertEqual(_code(r), "witness_required")

    def test_solo_start_allowed_once_witness_joins(self):
        self._mk()
        tt = self._tt()
        r = self._join_witness(tt["id"], "3")
        self.assertEqual(r.status_code, 200, r.text)
        self._admin(True)
        r = self._start_via_api(tt["id"])
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["started"])

    def test_two_player_start_needs_no_witness(self):
        self._mk()
        tt = self._tt()
        r = self.client.post(
            f"/api/tee-times/{tt['id']}/join", headers=self.h("2"))
        self.assertEqual(r.status_code, 200, r.text)
        self._admin(True)
        r = self._start_via_api(tt["id"])
        self.assertEqual(r.status_code, 200, r.text)

    def test_grandfathered_solo_start_needs_no_witness(self):
        self._mk()
        tt = self._tt()
        self._grandfather(tt["id"])
        self._admin(True)
        r = self._start_via_api(tt["id"])
        self.assertEqual(r.status_code, 200, r.text)

    # -- submit + co-submit ----------------------------------------------
    def _started_solo_with_witness(self):
        self._mk()
        tt = self._tt()
        self.assertEqual(self._join_witness(tt["id"], "3").status_code,
                         200)
        run(db.start_tournament_tee_time(self.db_path, tt["id"], "1"))
        return tt

    def test_solo_submit_awaits_witness_then_co_submit(self):
        tt = self._started_solo_with_witness()
        r = self._submit_stroke(tt["id"])
        self.assertEqual(r.status_code, 200, r.text)
        card = r.json()["card"]
        self.assertEqual(card["status"], "pending")
        self.assertTrue(card["awaiting_witness"])
        self.assertIsNotNone(card["submitted_at"])
        self.assertIsNone(card["witness_discord_id"])
        self.assertIsNone(card["witness_submitted_at"])
        # A non-witness can't co-submit.
        r = self.client.post(
            f"/api/scorecards/{card['id']}/witness-submit",
            headers=self.h("4"))
        self.assertEqual(r.status_code, 403, r.text)
        self.assertEqual(_code(r), "not_witness")
        # The joined witness co-submits: both timestamps now recorded.
        r = self.client.post(
            f"/api/scorecards/{card['id']}/witness-submit",
            headers=self.h("3"))
        self.assertEqual(r.status_code, 200, r.text)
        card = r.json()["card"]
        self.assertFalse(card["awaiting_witness"])
        self.assertEqual(card["witness_discord_id"], "3")
        self.assertIsNotNone(card["witness_submitted_at"])
        self.assertEqual(card["status"], "pending")
        # A second co-submit is a conflict — the card moved on.
        r = self.client.post(
            f"/api/scorecards/{card['id']}/witness-submit",
            headers=self.h("3"))
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(_code(r), "not_awaiting_witness")

    def test_partial_save_never_awaits_witness(self):
        tt = self._started_solo_with_witness()
        r = self.client.put(
            f"/api/tee-times/{tt['id']}/scorecard", headers=self.h("1"),
            json={"player_discord_id": "1",
                  "scores": [4] * 9 + [None] * 9, "round_number": 1})
        self.assertEqual(r.status_code, 200, r.text)
        card = r.json()["card"]
        self.assertEqual(card["status"], "in_progress")
        self.assertFalse(card["awaiting_witness"])

    def test_crew_verify_overrides_awaiting_witness(self):
        tt = self._started_solo_with_witness()
        r = self._submit_stroke(tt["id"])
        card = r.json()["card"]
        self.assertTrue(card["awaiting_witness"])
        self._admin(True)
        r = self.client.post(
            f"/api/admin/scorecards/{card['id']}/verify",
            headers=self.h("1"))
        self.assertEqual(r.status_code, 200, r.text)
        saved = run(db.get_scorecard(self.db_path, card["id"]))
        self.assertEqual(saved["status"], "verified")
        self.assertEqual(saved["awaiting_witness"], 0)
        # Honest record: no witness co-submit ever happened.
        self.assertIsNone(saved["witness_discord_id"])

    def test_solo_altshot_team_card_awaits_witness(self):
        self._mk(fmt="alt_shot")
        r = self.client.post(
            f"/api/tournaments/{self.tid}/teams", headers=self.h("1"),
            json={"partner_discord_id": "2"})
        self.assertEqual(r.status_code, 201, r.text)
        team = r.json()
        tt = self._tt(team_id=team["id"])
        self.assertEqual(self._join_witness(tt["id"], "3").status_code,
                         200)
        run(db.start_tournament_tee_time(self.db_path, tt["id"], "1"))
        # No witness_name needed anymore — and none given.
        r = self.client.put(
            f"/api/tee-times/{tt['id']}/scorecard", headers=self.h("1"),
            json={"team_id": team["id"], "scores": [4] * 18,
                  "round_number": 1, "complete": True})
        self.assertEqual(r.status_code, 200, r.text)
        card = r.json()["card"]
        self.assertEqual(card["status"], "pending")
        self.assertTrue(card["awaiting_witness"])
        r = self.client.post(
            f"/api/scorecards/{card['id']}/witness-submit",
            headers=self.h("3"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(r.json()["card"]["awaiting_witness"])


del ApiTestCase

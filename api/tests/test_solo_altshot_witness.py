"""Solo alt-shot witness rule (Cayden 2026-10-13) — GRANDFATHERED path.

A tournament alt-shot tee time holding a single team has nobody to
cross-check its card. A named witness stands in for the missing team.
Since 2026-10-17, NEW tee times use the joined-witness rule instead
(a witness joins the tee time and co-submits; see
test_witness_rule.py). Tee times created before that change keep this
named-witness behavior, so these tests grandfather their tee times
(witness_rule = 0) in _tt().

- A regular member's final submit without a witness -> 422
  witness_required; with a witness the card auto-verifies like a
  two-team round (and the tee time can archive).
- Partial (live) saves never need a witness.
- Crew/admin may still file a solo card without a witness — it stays
  pending (the standing rescue path).
- The rule is alt-shot only: a solo scramble card submitted without a
  witness stays pending, exactly as before.

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


class SoloAltShotWitnessTestCase(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.tid = None
        for uid, handle in [("1", "Alpha"), ("2", "Bravo"),
                            ("3", "Charlie"), ("4", "Delta")]:
            self.with_tz(uid)
            run(db.set_golfplus_handle(self.db_path, uid, handle))

    # -- helpers ------------------------------------------------------
    def _mk(self, fmt="alt_shot"):
        self.tid = run(db.create_tournament(
            self.db_path, GUILD, f"Solo {fmt}", fmt, 18,
            "Pebble Beach Golf Links", PARS_18, "solo", "1"))
        for uid in ["1", "2", "3", "4"]:
            r = self.client.post(
                f"/api/tournaments/{self.tid}/register",
                headers=self.h(uid))
            self.assertEqual(r.status_code, 200, r.text)
        return self.tid

    def _team(self, uid, partner_uid):
        r = self.client.post(
            f"/api/tournaments/{self.tid}/teams", headers=self.h(uid),
            json={"partner_discord_id": partner_uid})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    def _tt(self, uid, team_id):
        r = self.client.post(
            f"/api/tournaments/{self.tid}/tee-times", headers=self.h(uid),
            json={"label": "Sheet", "date": "2026-10-03", "time": "09:30",
                  "max_players": 4, "team_id": team_id})
        self.assertEqual(r.status_code, 200, r.text)
        tt = r.json()
        # Grandfather the tee time: this file covers the pre-2026-10-17
        # named-witness rule, which only applies to witness_rule = 0.
        run(db._execute(
            self.db_path,
            "UPDATE tee_times SET witness_rule = 0 WHERE id = ?",
            (tt["id"],)))
        return tt

    def _solo(self, fmt="alt_shot"):
        self._mk(fmt)
        team = self._team("1", "2")
        tt = self._tt("1", team["id"])
        run(db.start_tournament_tee_time(self.db_path, tt["id"], "1"))
        return team, tt

    def _submit(self, tt_id, team_id, uid, **kw):
        body = {"team_id": team_id, "scores": [4] * 18, "round_number": 1,
                "complete": True}
        body.update(kw)
        return self.client.put(f"/api/tee-times/{tt_id}/scorecard",
                               headers=self.h(uid), json=body)

    # -- the rule -------------------------------------------------------
    def test_solo_submit_without_witness_422(self):
        team, tt = self._solo()
        r = self._submit(tt["id"], team["id"], "1")
        self.assertEqual(r.status_code, 422, r.text)
        self.assertEqual(_code(r), "witness_required")

    def test_solo_submit_blank_witness_422(self):
        team, tt = self._solo()
        r = self._submit(tt["id"], team["id"], "1", witness_name="   ")
        self.assertEqual(r.status_code, 422, r.text)
        self.assertEqual(_code(r), "witness_required")

    def test_solo_submit_with_witness_verified(self):
        team, tt = self._solo()
        r = self._submit(tt["id"], team["id"], "1",
                         witness_name="Charlie")
        self.assertEqual(r.status_code, 200, r.text)
        card = r.json()["card"]
        self.assertEqual(card["status"], "verified")
        self.assertEqual(card["witness_name"], "Charlie")

    def test_solo_partial_save_needs_no_witness(self):
        team, tt = self._solo()
        r = self.client.put(
            f"/api/tee-times/{tt['id']}/scorecard", headers=self.h("1"),
            json={"team_id": team["id"],
                  "scores": [4] * 9 + [None] * 9, "round_number": 1})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["card"]["status"], "in_progress")

    def test_solo_crew_submit_without_witness_stays_pending(self):
        team, tt = self._solo()
        self._crew(True)
        r = self._submit(tt["id"], team["id"], "1")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["card"]["status"], "pending")

    def test_witnessed_solo_card_archives_tee_time(self):
        team, tt = self._solo()
        r = self._submit(tt["id"], team["id"], "2",
                         witness_name="Charlie")
        self.assertEqual(r.status_code, 200, r.text)
        tt_now = run(db.get_tee_time(self.db_path, tt["id"]))
        self.assertIsNotNone(tt_now.get("archived_at"))

    # -- unchanged neighbours ------------------------------------------
    def test_two_team_submit_without_witness_still_verified(self):
        self._mk()
        t1 = self._team("1", "2")
        t2 = self._team("3", "4")
        tt = self._tt("1", t1["id"])
        r = self.client.post(f"/api/tee-times/{tt['id']}/join",
                             headers=self.h("3"),
                             json={"team_id": t2["id"]})
        self.assertEqual(r.status_code, 200, r.text)
        run(db.start_tournament_tee_time(self.db_path, tt["id"], "1"))
        r = self._submit(tt["id"], t1["id"], "1")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["card"]["status"], "verified")

    def test_solo_scramble_without_witness_stays_pending(self):
        team, tt = self._solo(fmt="scramble")
        r = self._submit(tt["id"], team["id"], "1")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["card"]["status"], "pending")


del ApiTestCase

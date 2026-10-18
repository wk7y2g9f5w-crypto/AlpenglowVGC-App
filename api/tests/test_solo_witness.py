"""Solo-role witness input (Cayden 2026-10-18).

A player holding the Discord "Solo" role may input a witness for
their OWN solo tournament tee time — the one exception to "the player
can never assign a witness" from the joined-witness rule:

- The named witness must be crew (admin / Mod / Tournament Director);
  regular players can't be named this way. The self-join path is
  unchanged — anyone may still join as witness themselves.
- The input locks the moment the round starts: no adding, removing,
  or swapping the witness afterwards.
- The designated witness co-submits the card like any witness.
- Any member of a solo alt-shot team who holds the role may input the
  team's witness (not just the sheet rep).

fetch_solo_status / fetch_crew_status are replaced with set-driven
fakes: SOLO = {"1"}, CREW = {"2", "4"} unless a test adjusts them.

Fresh temp DB, monkeypatched fetch_* — see test_api.ApiTestCase.
Run with: python -m pytest api/tests/ -q
"""
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR))

from test_api import ApiTestCase, run, GUILD, main, \
    fake_fetch_crew_status, fake_fetch_solo_status  # noqa: E402

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


class SoloWitnessTestCase(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.SOLO = {"1"}
        self.CREW = {"2", "4"}
        self.CREW_DOWN = False

        case = self

        async def fake_solo(discord_id):
            return str(discord_id) in case.SOLO

        async def fake_crew(discord_id):
            if case.CREW_DOWN:
                return None
            return str(discord_id) in case.CREW

        self.addCleanup(setattr, main, "fetch_solo_status",
                        fake_fetch_solo_status)
        self.addCleanup(setattr, main, "fetch_crew_status",
                        fake_fetch_crew_status)
        main.fetch_solo_status = fake_solo
        main.fetch_crew_status = fake_crew

        self.tid = None
        for uid in ["1", "2", "3", "4", "5"]:
            self.with_tz(uid)
            run(db.set_golfplus_handle(
                self.db_path, uid, f"Handle{uid}"))

    # -- helpers ------------------------------------------------------
    def _mk(self, fmt="stroke"):
        self.tid = run(db.create_tournament(
            self.db_path, GUILD, f"Solo {fmt}", fmt, 18,
            "Pebble Beach Golf Links", PARS_18, "witness", "1"))
        for uid in ["1", "2", "3", "4", "5"]:
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

    def _designate(self, tt_id, target, uid="1"):
        return self.client.post(
            f"/api/tee-times/{tt_id}/witness/designate",
            headers=self.h(uid), json={"player_id": target})

    def _undesignate(self, tt_id, target, uid="1"):
        return self.client.delete(
            f"/api/tee-times/{tt_id}/witness/designate/{target}",
            headers=self.h(uid))

    def _candidates(self, tt_id, uid="1"):
        return self.client.get(
            f"/api/tee-times/{tt_id}/witness-candidates",
            headers=self.h(uid))

    # -- the happy path -------------------------------------------------
    def test_designate_crew_witness_and_listed(self):
        self._mk()
        tt = self._tt()
        r = self._designate(tt["id"], "2")
        self.assertEqual(r.status_code, 200, r.text)
        listed = self._listed_tt(tt["id"])
        self.assertEqual(len(listed["witnesses"]), 1)
        w = listed["witnesses"][0]
        self.assertEqual(w["discord_id"], "2")
        self.assertEqual(w["added_by"], "1")
        self.assertIsNotNone(w["joined_at"])
        # Designating the same person again is a no-op success.
        r = self._designate(tt["id"], "2")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["already"])

    def test_candidates_are_crew_non_participants_only(self):
        self._mk()
        tt = self._tt()
        r = self._candidates(tt["id"])
        self.assertEqual(r.status_code, 200, r.text)
        ids = {p["discord_id"] for p in r.json()}
        self.assertIn("2", ids)
        self.assertIn("4", ids)
        self.assertNotIn("1", ids)   # the player themselves
        self.assertNotIn("3", ids)   # regular player
        self.assertNotIn("5", ids)   # regular player

    def test_designated_witness_gates_start_and_co_submits(self):
        self._mk()
        tt = self._tt()
        # No witness yet: the start gate still applies.
        r = self.client.post(f"/api/tee-times/{tt['id']}/start",
                             headers=self.h("1"))
        self.assertEqual(r.status_code, 422, r.text)
        self.assertEqual(_code(r), "witness_required")
        self.assertEqual(self._designate(tt["id"], "2").status_code, 200)
        r = self.client.post(f"/api/tee-times/{tt['id']}/start",
                             headers=self.h("1"))
        self.assertEqual(r.status_code, 200, r.text)
        # Submit: the card waits for the designated witness.
        r = self.client.put(
            f"/api/tee-times/{tt['id']}/scorecard", headers=self.h("1"),
            json={"player_discord_id": "1", "scores": [4] * 18,
                  "round_number": 1, "complete": True})
        self.assertEqual(r.status_code, 200, r.text)
        card = r.json()["card"]
        self.assertTrue(card["awaiting_witness"])
        r = self.client.post(
            f"/api/scorecards/{card['id']}/witness-submit",
            headers=self.h("2"))
        self.assertEqual(r.status_code, 200, r.text)
        card = r.json()["card"]
        self.assertFalse(card["awaiting_witness"])
        self.assertEqual(card["witness_discord_id"], "2")
        self.assertIsNotNone(card["witness_submitted_at"])

    # -- the gates -------------------------------------------------------
    def test_designate_requires_solo_role(self):
        self._mk()
        tt = self._tt(uid="3")   # "3" plays, but holds no Solo role
        r = self._designate(tt["id"], "2", uid="3")
        self.assertEqual(r.status_code, 403, r.text)
        self.assertEqual(_code(r), "solo_role_required")
        r = self._candidates(tt["id"], uid="3")
        self.assertEqual(r.status_code, 403, r.text)
        self.assertEqual(_code(r), "solo_role_required")

    def test_designate_requires_participation(self):
        self._mk()
        tt = self._tt()
        r = self._designate(tt["id"], "2", uid="3")
        self.assertEqual(r.status_code, 403, r.text)
        self.assertEqual(_code(r), "not_on_tee_time")

    def test_designate_target_must_be_crew(self):
        self._mk()
        tt = self._tt()
        r = self._designate(tt["id"], "3")
        self.assertEqual(r.status_code, 403, r.text)
        self.assertEqual(_code(r), "witness_must_be_crew")

    def test_designate_target_cannot_be_a_player(self):
        self._mk()
        tt = self._tt()
        r = self._designate(tt["id"], "1")
        self.assertEqual(r.status_code, 403, r.text)
        self.assertEqual(_code(r), "is_player")

    def test_designate_unknown_player_404(self):
        self._mk()
        tt = self._tt()
        r = self._designate(tt["id"], "99")
        self.assertEqual(r.status_code, 404, r.text)

    def test_designate_crew_check_unavailable_is_503(self):
        self._mk()
        tt = self._tt()
        self.CREW_DOWN = True
        r = self._designate(tt["id"], "2")
        self.assertEqual(r.status_code, 503, r.text)

    def test_designate_grandfathered_tee_time_409(self):
        self._mk()
        tt = self._tt()
        self._grandfather(tt["id"])
        r = self._designate(tt["id"], "2")
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(_code(r), "witness_not_required")

    # -- the start lock ---------------------------------------------------
    def test_designation_locks_once_round_starts(self):
        self._mk()
        tt = self._tt()
        self.assertEqual(self._designate(tt["id"], "2").status_code, 200)
        run(db.start_tournament_tee_time(self.db_path, tt["id"], "1"))
        r = self._designate(tt["id"], "4")
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(_code(r), "round_started")
        r = self._undesignate(tt["id"], "2")
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(_code(r), "round_started")

    def test_undesignate_own_designation_only(self):
        self._mk()
        tt = self._tt()
        self.assertEqual(self._designate(tt["id"], "2").status_code, 200)
        # Someone else can't pull it off.
        r = self._undesignate(tt["id"], "2", uid="3")
        self.assertEqual(r.status_code, 403, r.text)
        self.assertEqual(_code(r), "not_your_witness")
        # The designator can, before the start.
        r = self._undesignate(tt["id"], "2")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["removed"])
        self.assertEqual(self._listed_tt(tt["id"])["witnesses"], [])
        # A witness added by someone else (admin path, simulated at the
        # db layer) is equally out of the designator's reach.
        run(db.add_tee_time_witness(self.db_path, tt["id"], "4",
                                    added_by="9"))
        r = self._undesignate(tt["id"], "4")
        self.assertEqual(r.status_code, 403, r.text)
        self.assertEqual(_code(r), "not_your_witness")

    # -- other paths unchanged ---------------------------------------------
    def test_regular_player_can_still_self_join(self):
        self._mk()
        tt = self._tt()
        r = self.client.post(f"/api/tee-times/{tt['id']}/witness",
                             headers=self.h("3"))
        self.assertEqual(r.status_code, 200, r.text)

    def test_me_reports_solo_role(self):
        r = self.client.get("/api/players/me", headers=self.h("1"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["is_solo"])
        r = self.client.get("/api/players/me", headers=self.h("3"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(r.json()["is_solo"])

    # -- alt-shot solo team: any member with the role ----------------------
    def test_altshot_team_partner_with_role_can_designate(self):
        self._mk(fmt="alt_shot")
        r = self.client.post(
            f"/api/tournaments/{self.tid}/teams", headers=self.h("1"),
            json={"partner_discord_id": "2"})
        self.assertEqual(r.status_code, 201, r.text)
        team = r.json()
        tt = self._tt(team_id=team["id"])
        # The rep ("1") holds no Solo role here; the partner ("2") does.
        self.SOLO = {"2"}
        r = self._designate(tt["id"], "4", uid="1")
        self.assertEqual(r.status_code, 403, r.text)
        self.assertEqual(_code(r), "solo_role_required")
        r = self._designate(tt["id"], "4", uid="2")
        self.assertEqual(r.status_code, 200, r.text)
        listed = self._listed_tt(tt["id"])
        self.assertEqual(listed["witnesses"][0]["discord_id"], "4")
        self.assertEqual(listed["witnesses"][0]["added_by"], "2")
        # ...and a fellow team member still can't be the witness.
        r = self._designate(tt["id"], "1", uid="2")
        self.assertEqual(r.status_code, 403, r.text)
        self.assertEqual(_code(r), "is_player")


del ApiTestCase

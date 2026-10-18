"""Solo-role witness exemption (Cayden 2026-10-18).

A player holding the Discord "Solo" role needs NO witness at all —
this replaced the same-day "input your own witness" path at Cayden's
direction:

- No witness required to START a solo tournament round.
- No witness co-submit afterwards: the card goes straight to the
  normal crew-verify pile (pending, awaiting_witness False).
- For a solo alt-shot team, ANY member holding the role covers the
  team.
- Players without the role are unchanged: a joined witness is still
  required to start, and their final submit parks at
  awaiting_witness for the co-submit.
- An unknown role check (Discord unreachable) counts as NOT exempt —
  the witness rule applies (fail closed).
- The designate endpoints are gone (404).

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
        self.SOLO_DOWN = False

        case = self

        async def fake_solo(discord_id):
            if case.SOLO_DOWN:
                return None
            return str(discord_id) in case.SOLO

        async def fake_crew(discord_id):
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

    def _start(self, tt_id, uid="1"):
        return self.client.post(f"/api/tee-times/{tt_id}/start",
                                headers=self.h(uid))

    def _submit(self, tt_id, uid="1", **kw):
        body = {"player_discord_id": uid, "scores": [4] * 18,
                "round_number": 1, "complete": True}
        body.update(kw)
        return self.client.put(
            f"/api/tee-times/{tt_id}/scorecard", headers=self.h(uid),
            json=body)

    # -- the exemption --------------------------------------------------
    def test_solo_holder_starts_without_witness(self):
        self._mk()
        tt = self._tt()  # "1" holds Solo by default
        r = self._start(tt["id"])
        self.assertEqual(r.status_code, 200, r.text)

    def test_solo_holder_submit_skips_witness_wait(self):
        self._mk()
        tt = self._tt()
        self.assertEqual(self._start(tt["id"]).status_code, 200)
        r = self._submit(tt["id"])
        self.assertEqual(r.status_code, 200, r.text)
        card = r.json()["card"]
        self.assertEqual(card["status"], "pending")
        self.assertFalse(card["awaiting_witness"])

    def test_solo_holder_with_joined_witness_still_skips_wait(self):
        # A witness joining anyway doesn't drag a Solo holder's card
        # into the co-submit flow — the exemption wins.
        self._mk()
        tt = self._tt()
        r = self.client.post(f"/api/tee-times/{tt['id']}/witness",
                             headers=self.h("3"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self._start(tt["id"]).status_code, 200)
        r = self._submit(tt["id"])
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(r.json()["card"]["awaiting_witness"])

    def test_altshot_team_covered_by_any_members_role(self):
        self._mk(fmt="alt_shot")
        r = self.client.post(
            f"/api/tournaments/{self.tid}/teams", headers=self.h("1"),
            json={"partner_discord_id": "2"})
        self.assertEqual(r.status_code, 201, r.text)
        team = r.json()
        tt = self._tt(team_id=team["id"])
        # Only the partner ("2") holds the role; the rep starts.
        self.SOLO = {"2"}
        r = self._start(tt["id"], uid="1")
        self.assertEqual(r.status_code, 200, r.text)
        r = self._submit(tt["id"], uid="1", team_id=team["id"],
                         player_discord_id=None)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(r.json()["card"]["awaiting_witness"])

    def test_role_check_unavailable_fails_closed(self):
        self._mk()
        tt = self._tt()
        self.SOLO_DOWN = True
        r = self._start(tt["id"])
        self.assertEqual(r.status_code, 422, r.text)
        self.assertEqual(_code(r), "witness_required")

    # -- non-holders are unchanged --------------------------------------
    def test_non_holder_still_needs_witness(self):
        self._mk()
        tt = self._tt(uid="3")  # "3" holds no Solo role
        r = self._start(tt["id"], uid="3")
        self.assertEqual(r.status_code, 422, r.text)
        self.assertEqual(_code(r), "witness_required")
        # With a joined witness the round starts, and the final submit
        # still parks for the co-submit.
        r = self.client.post(f"/api/tee-times/{tt['id']}/witness",
                             headers=self.h("2"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self._start(tt["id"], uid="3").status_code, 200)
        r = self._submit(tt["id"], uid="3")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["card"]["awaiting_witness"])

    def test_regular_player_can_still_self_join(self):
        self._mk()
        tt = self._tt()
        r = self.client.post(f"/api/tee-times/{tt['id']}/witness",
                             headers=self.h("3"))
        self.assertEqual(r.status_code, 200, r.text)

    # -- the removed input path ------------------------------------------
    def test_designate_endpoints_are_gone(self):
        # The SPA catch-all answers unknown paths (405 for POST/DELETE,
        # the app shell for GET) — what matters is none of the removed
        # endpoints respond as API routes anymore.
        self._mk()
        tt = self._tt()
        r = self.client.post(
            f"/api/tee-times/{tt['id']}/witness/designate",
            headers=self.h("1"), json={"player_id": "2"})
        self.assertIn(r.status_code, (404, 405), r.text)
        r = self.client.get(
            f"/api/tee-times/{tt['id']}/witness-candidates",
            headers=self.h("1"))
        # No API route matches anymore: the SPA fallback answers 404
        # ("web app not deployed" in tests) instead of a candidate list.
        self.assertEqual(r.status_code, 404, r.text)
        r = self.client.delete(
            f"/api/tee-times/{tt['id']}/witness/designate/2",
            headers=self.h("1"))
        self.assertIn(r.status_code, (404, 405), r.text)

    def test_me_reports_solo_role(self):
        r = self.client.get("/api/players/me", headers=self.h("1"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["is_solo"])
        r = self.client.get("/api/players/me", headers=self.h("3"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(r.json()["is_solo"])


del ApiTestCase

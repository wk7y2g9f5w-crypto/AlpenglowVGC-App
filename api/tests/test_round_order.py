"""Tests for the play-in-order rule: rounds must be played sequentially.

- Creating/joining a Round N>1 tee time needs a submitted (pending or
  verified) card for Round N-1 — admins bypass.
- Submitting a Round N>1 card needs the same — crew submitting on
  someone's behalf bypass (admin override).
- Approving a join request enforces the requester's previous round unless
  the approver is an admin.
- has_completed_round_card unit checks.
"""

from test_api import ApiTestCase as _ApiTestCase, run, main, GUILD
from src import db

PARS_18 = ",".join(["4"] * 18)

# R1 closed, R2 + R3 open: the order gate can be tested on live rounds.
ROUNDS = [
    {"start_date": "2026-01-01", "end_date": "2026-01-02"},
    {"start_date": "2026-01-03", "end_date": "2026-12-31"},
    {"start_date": "2026-02-01", "end_date": "2026-12-31"},
]


class RoundOrderApiTestCase(_ApiTestCase):
    def _tourney(self):
        # Tee-time creation requires a timezone on the acting user.
        for uid in ("123", "456", "999"):
            self.with_tz(uid)
        return run(db.create_tournament(
            self.db_path, GUILD, "Order", "stroke", 18,
            "Pebble Beach Golf Links", PARS_18, None, "1",
            rounds=ROUNDS))

    def _tt(self, tid, rnd, creator="123", label="flight"):
        tt_id = run(db.create_tee_time(
            self.db_path, tid, label, "2026-01-02T15:30:00+00:00",
            4, creator, None, round_number=rnd))
        run(db.join_tee_time(self.db_path, tt_id, creator))
        return tt_id

    def _start(self, tt_id, creator="123"):
        r = self.client.post(f"/api/tee-times/{tt_id}/start",
                             headers=self.h(creator))
        self.assertEqual(r.status_code, 200, r.text)

    def _submit(self, tid, pid, rnd, *, complete=True):
        """A submitted (verified) card for pid/round, via the db layer."""
        tt_id = self._tt(tid, rnd, creator=pid, label=f"r{rnd}-{pid}")
        return run(db.upsert_scorecard(
            self.db_path, tid, pid, None, tt_id, [4] * 18, "verified",
            submitted_by=pid, round_number=rnd))

    # -- has_completed_round_card -------------------------------------

    def test_completed_card_counts(self):
        tid = self._tourney()
        self.assertFalse(run(db.has_completed_round_card(
            self.db_path, tid, "123", 1)))
        self._submit(tid, "123", 1)
        self.assertTrue(run(db.has_completed_round_card(
            self.db_path, tid, "123", 1)))
        self.assertFalse(run(db.has_completed_round_card(
            self.db_path, tid, "123", 2)))

    def test_in_progress_does_not_count(self):
        tid = self._tourney()
        tt_id = self._tt(tid, 1)
        run(db.save_partial_scorecard(
            self.db_path, tid, "123", None, tt_id, [4] + [None] * 17,
            submitted_by="123", round_number=1))
        self.assertFalse(run(db.has_completed_round_card(
            self.db_path, tid, "123", 1)))

    def test_pending_counts(self):
        tid = self._tourney()
        tt_id = self._tt(tid, 1)
        run(db.upsert_scorecard(
            self.db_path, tid, "123", None, tt_id, [4] * 18, "pending",
            submitted_by="123", round_number=1))
        self.assertTrue(run(db.has_completed_round_card(
            self.db_path, tid, "123", 1)))

    # -- create tee time ----------------------------------------------

    def test_create_round2_without_round1_403(self):
        tid = self._tourney()
        r = self.client.post(
            f"/api/tournaments/{tid}/tee-times",
            headers=self.h("123"),
            json={"label": "r2", "date": "2026-06-01", "time": "15:30",
                  "max_players": 4, "round_number": 2})
        self.assertEqual(r.status_code, 403, r.text)
        self.assertEqual(r.json()["code"], "previous_round_incomplete")

    def test_create_round2_with_round1_ok(self):
        tid = self._tourney()
        self._submit(tid, "123", 1)
        r = self.client.post(
            f"/api/tournaments/{tid}/tee-times",
            headers=self.h("123"),
            json={"label": "r2", "date": "2026-06-01", "time": "15:30",
                  "max_players": 4, "round_number": 2})
        self.assertEqual(r.status_code, 200, r.text)

    def test_create_round1_always_open(self):
        tid = self._tourney()
        r = self.client.post(
            f"/api/tournaments/{tid}/tee-times",
            headers=self.h("999"),
            json={"label": "r1", "date": "2026-06-01", "time": "15:30",
                  "max_players": 4, "round_number": 1})
        self.assertEqual(r.status_code, 200, r.text)

    # -- join ----------------------------------------------------------

    def test_join_round2_without_round1_403(self):
        tid = self._tourney()
        tt_id = self._tt(tid, 2, creator="456")
        r = self.client.post(f"/api/tee-times/{tt_id}/join",
                             headers=self.h("123"))
        self.assertEqual(r.status_code, 403, r.text)
        self.assertEqual(r.json()["code"], "previous_round_incomplete")

    def test_join_round2_with_round1_ok(self):
        tid = self._tourney()
        self._submit(tid, "123", 1)
        tt_id = self._tt(tid, 2, creator="456")
        r = self.client.post(f"/api/tee-times/{tt_id}/join",
                             headers=self.h("123"))
        self.assertEqual(r.status_code, 200, r.text)

    # -- score submit ---------------------------------------------------

    def _started_r2(self, tid, pid):
        tt_id = self._tt(tid, 2, creator=pid)
        self._start(tt_id, creator=pid)
        return tt_id

    def test_submit_round2_without_round1_403(self):
        tid = self._tourney()
        tt_id = self._started_r2(tid, "123")
        r = self.client.put(
            f"/api/tee-times/{tt_id}/scorecard", headers=self.h("123"),
            json={"player_discord_id": "123", "scores": [4] * 18,
                  "round_number": 2, "complete": True})
        self.assertEqual(r.status_code, 403, r.text)
        self.assertEqual(r.json()["code"], "previous_round_incomplete")

    def test_submit_round2_with_round1_ok(self):
        tid = self._tourney()
        self._submit(tid, "123", 1)
        tt_id = self._started_r2(tid, "123")
        r = self.client.put(
            f"/api/tee-times/{tt_id}/scorecard", headers=self.h("123"),
            json={"player_discord_id": "123", "scores": [4] * 18,
                  "round_number": 2, "complete": True})
        self.assertEqual(r.status_code, 200, r.text)

    def test_admin_submit_bypasses_order_gate(self):
        # Admin override after the fact: an admin can enter a card for a
        # player who missed the previous round, without being in the tee
        # time (Gate 1 admin bypass + Gate 3f crew bypass).
        async def admin_only_999(discord_id):
            return discord_id == "999"

        tid = self._tourney()
        tt_id = self._started_r2(tid, "123")
        # In production crew status includes admins; mirror that here since
        # Gate 3f checks crew status.
        main.fetch_admin_status = admin_only_999
        main.fetch_crew_status = admin_only_999
        r = self.client.put(
            f"/api/tee-times/{tt_id}/scorecard", headers=self.h("999"),
            json={"player_discord_id": "123", "scores": [4] * 18,
                  "round_number": 2, "complete": True})
        self.assertEqual(r.status_code, 200, r.text)

    def test_crew_in_tee_time_submit_bypasses_order_gate(self):
        # Crew (not admin) submitting on a playing partner's behalf from
        # inside the tee time also bypasses the order gate (Gate 3f), while
        # Gate 1 still requires tee-time membership.
        async def crew_only_999(discord_id):
            return discord_id == "999"

        tid = self._tourney()
        # Join before Start Round: starting locks joining.
        tt_id = self._tt(tid, 2, creator="123")
        run(db.join_tee_time(self.db_path, tt_id, "999"))
        self._start(tt_id, creator="123")
        main.fetch_crew_status = crew_only_999
        r = self.client.put(
            f"/api/tee-times/{tt_id}/scorecard", headers=self.h("999"),
            json={"player_discord_id": "123", "scores": [4] * 18,
                  "round_number": 2, "complete": True})
        self.assertEqual(r.status_code, 200, r.text)

    # -- join request approve -------------------------------------------

    def test_approve_request_enforces_order_for_creator(self):
        tid = self._tourney()
        tt_id = self._tt(tid, 2, creator="456")
        run(db.register_player(self.db_path, tid, "123"))
        self.assertEqual(
            run(db.create_join_request(self.db_path, tt_id, "123")), "ok")
        req_id = run(
            db.list_pending_join_requests(self.db_path, tt_id))[0]["id"]
        r = self.client.post(
            f"/api/tee-times/{tt_id}/requests/{req_id}/approve",
            headers=self.h("456"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["status"], "declined")
        # Requester was not added.
        players = run(db.get_tee_time_players(self.db_path, tt_id))
        self.assertNotIn("123", [p["discord_id"] for p in players])

    def test_admin_approve_bypasses_order_gate(self):
        async def admin_only_456(discord_id):
            return discord_id == "456"

        main.fetch_admin_status = admin_only_456
        tid = self._tourney()
        tt_id = self._tt(tid, 2, creator="456")
        run(db.register_player(self.db_path, tid, "123"))
        self.assertEqual(
            run(db.create_join_request(self.db_path, tt_id, "123")), "ok")
        req_id = run(
            db.list_pending_join_requests(self.db_path, tt_id))[0]["id"]
        r = self.client.post(
            f"/api/tee-times/{tt_id}/requests/{req_id}/approve",
            headers=self.h("456"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["status"], "accepted")
        players = run(db.get_tee_time_players(self.db_path, tt_id))
        self.assertIn("123", [p["discord_id"] for p in players])


# Prevent pytest from re-collecting the whole inherited ApiTestCase suite
# under this module (see AGENTS.md).
del _ApiTestCase

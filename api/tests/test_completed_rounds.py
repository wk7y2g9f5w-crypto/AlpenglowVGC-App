"""Tests for my_completed_rounds on GET /api/tournaments.

The app hides rounds the signed-in user has already finished from the
tee-time round picker. "Finished" = a submitted card (pending or
verified): the player's own card, or a team card for any team they
belong to in the tournament. In-progress cards don't count.
"""

from test_api import ApiTestCase as _ApiTestCase, run, GUILD
from src import db

PARS_18 = ",".join(["4"] * 18)

ROUNDS = [
    {"start_date": "2026-01-01", "end_date": "2026-01-02"},
    {"start_date": "2026-01-03", "end_date": "2026-12-31"},
    {"start_date": "2026-02-01", "end_date": "2026-12-31"},
]


class CompletedRoundsApiTestCase(_ApiTestCase):
    def _tourney(self):
        for uid in ("123", "456", "999"):
            self.with_tz(uid)
        return run(db.create_tournament(
            self.db_path, GUILD, "Completed", "stroke", 18,
            "Pebble Beach Golf Links", PARS_18, None, "1",
            rounds=ROUNDS))

    def _completed(self, tid, uid):
        r = self.client.get("/api/tournaments", headers=self.h(uid))
        self.assertEqual(r.status_code, 200, r.text)
        for t in r.json():
            if t["id"] == tid:
                return t["my_completed_rounds"]
        self.fail(f"tournament {tid} missing from list")

    def _submit(self, tid, pid, rnd, *, status="verified", team_id=None):
        tt_id = run(db.create_tee_time(
            self.db_path, tid, f"r{rnd}-{pid or team_id}",
            "2026-01-02T15:30:00+00:00", 4, pid or "123", None,
            round_number=rnd))
        return run(db.upsert_scorecard(
            self.db_path, tid, pid, team_id, tt_id, [4] * 18, status,
            submitted_by=pid or "123", round_number=rnd))

    def test_no_cards_empty(self):
        tid = self._tourney()
        self.assertEqual(self._completed(tid, "123"), [])

    def test_own_submitted_card_listed(self):
        tid = self._tourney()
        self._submit(tid, "123", 1)
        self.assertEqual(self._completed(tid, "123"), [1])
        # Another player is unaffected.
        self.assertEqual(self._completed(tid, "456"), [])

    def test_pending_counts(self):
        tid = self._tourney()
        self._submit(tid, "123", 2, status="pending")
        self.assertEqual(self._completed(tid, "123"), [2])

    def test_in_progress_does_not_count(self):
        tid = self._tourney()
        tt_id = run(db.create_tee_time(
            self.db_path, tid, "live", "2026-01-02T15:30:00+00:00",
            4, "123", None, round_number=1))
        run(db.save_partial_scorecard(
            self.db_path, tid, "123", None, tt_id, [4] + [None] * 17,
            submitted_by="123", round_number=1))
        self.assertEqual(self._completed(tid, "123"), [])

    def test_team_card_counts_for_every_member(self):
        tid = self._tourney()
        team_id = run(db.create_team(self.db_path, tid, "Team A", "123"))
        run(db.add_team_member(self.db_path, team_id, "123"))
        run(db.add_team_member(self.db_path, team_id, "456"))
        self._submit(tid, None, 1, team_id=team_id)
        self.assertEqual(self._completed(tid, "123"), [1])
        self.assertEqual(self._completed(tid, "456"), [1])
        self.assertEqual(self._completed(tid, "999"), [])

    def test_helper_sorted(self):
        tid = self._tourney()
        self._submit(tid, "123", 3)
        self._submit(tid, "123", 1)
        self.assertEqual(
            run(db.completed_round_numbers(self.db_path, tid, "123")),
            [1, 3])


# Prevent pytest from re-collecting the whole inherited ApiTestCase suite
# under this module (see AGENTS.md).
del _ApiTestCase

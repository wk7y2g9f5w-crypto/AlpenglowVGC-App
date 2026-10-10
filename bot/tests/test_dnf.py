"""Play-in-order + DNF tests: dnf_rows and the ranked-board exclusion.

- A registered player with no Round 1 card once Round 1 closes is DNF.
- A player who completed Round 1 but missed a closed Round 2 is DNF.
- A player missing only an OPEN round is not DNF.
- DNF players are excluded from _stroke_ranked (and thus from season
  points, which build on the ranked list).
"""
import os
import tempfile
import unittest

from src import db
from src import leaderboard_render as lr

PARS_18 = ",".join(["4"] * 18)

ROUNDS = [
    {"start_date": "2026-01-01", "end_date": "2026-01-02"},  # closed
    {"start_date": "2026-01-03", "end_date": "2026-01-04"},  # closed
    {"start_date": "2026-10-01", "end_date": "2026-12-31"},  # open
]


class DnfTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.db")
        await db.init_db(self.db_path)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def _tourney(self):
        tid = await db.create_tournament(
            self.db_path, "guild1", "DNF", "stroke", 18,
            "Pebble Beach Golf Links", PARS_18, None, "admin1",
            rounds=ROUNDS)
        await db.set_tournament_status(self.db_path, tid, "in_progress")
        return tid

    async def _card(self, tid, pid, rnd, status="verified"):
        tt_id = await db.create_tee_time(
            self.db_path, tid, f"r{rnd}-{pid}",
            "2026-01-02T15:30:00+00:00", 4, pid, None, round_number=rnd)
        await db.join_tee_time(self.db_path, tt_id, pid)
        await db.upsert_scorecard(
            self.db_path, tid, pid, None, tt_id, [4] * 18, status,
            submitted_by=pid, round_number=rnd)

    async def test_no_show_round1_is_dnf(self):
        tid = await self._tourney()
        await db.register_player(self.db_path, tid, "p1")
        t = await db.get_tournament(self.db_path, tid)
        dnf = await lr.dnf_rows(self.db_path, t)
        self.assertEqual(len(dnf), 1)
        self.assertEqual(dnf[0]["player_discord_id"], "p1")
        self.assertEqual(dnf[0]["missed_round"], 1)

    async def test_missed_round2_is_dnf(self):
        tid = await self._tourney()
        await db.register_player(self.db_path, tid, "p1")
        await self._card(tid, "p1", 1)  # played R1, skipped closed R2
        t = await db.get_tournament(self.db_path, tid)
        dnf = await lr.dnf_rows(self.db_path, t)
        self.assertEqual(len(dnf), 1)
        self.assertEqual(dnf[0]["missed_round"], 2)

    async def test_missing_open_round_is_not_dnf(self):
        tid = await self._tourney()
        await db.register_player(self.db_path, tid, "p1")
        await self._card(tid, "p1", 1)
        await self._card(tid, "p1", 2)
        # R3 still open and unplayed: not a miss.
        t = await db.get_tournament(self.db_path, tid)
        self.assertEqual(await lr.dnf_rows(self.db_path, t), [])

    async def test_pending_card_counts_as_played(self):
        tid = await self._tourney()
        await db.register_player(self.db_path, tid, "p1")
        await self._card(tid, "p1", 1, status="pending")
        await self._card(tid, "p1", 2, status="pending")
        t = await db.get_tournament(self.db_path, tid)
        self.assertEqual(await lr.dnf_rows(self.db_path, t), [])

    async def test_in_progress_does_not_count(self):
        tid = await self._tourney()
        await db.register_player(self.db_path, tid, "p1")
        tt_id = await db.create_tee_time(
            self.db_path, tid, "r1-p1", "2026-01-02T15:30:00+00:00",
            4, "p1", None, round_number=1)
        await db.join_tee_time(self.db_path, tt_id, "p1")
        await db.save_partial_scorecard(
            self.db_path, tid, "p1", None, tt_id, [4] + [None] * 17,
            submitted_by="p1", round_number=1)
        t = await db.get_tournament(self.db_path, tid)
        dnf = await lr.dnf_rows(self.db_path, t)
        self.assertEqual(len(dnf), 1)
        self.assertEqual(dnf[0]["missed_round"], 1)

    async def test_dnf_excluded_from_ranked(self):
        tid = await self._tourney()
        for pid in ("p1", "p2"):
            await db.register_player(self.db_path, tid, pid)
        await self._card(tid, "p1", 1)
        await self._card(tid, "p1", 2)
        await self._card(tid, "p2", 1)  # p2 missed closed R2 -> DNF
        t = await db.get_tournament(self.db_path, tid)
        ranked, _ = await lr._stroke_ranked(self.db_path, t)
        self.assertEqual([r["player_discord_id"] for r in ranked], ["p1"])
        dnf = await lr.dnf_rows(self.db_path, t)
        self.assertEqual([d["player_discord_id"] for d in dnf], ["p2"])

    async def test_unregistered_card_holder_can_dnf(self):
        # Played R1 via a tee time but never registered; still DNF on a
        # missed closed round and still leaves the ranked board.
        tid = await self._tourney()
        await self._card(tid, "p1", 1)
        t = await db.get_tournament(self.db_path, tid)
        dnf = await lr.dnf_rows(self.db_path, t)
        self.assertEqual(len(dnf), 1)
        self.assertEqual(dnf[0]["missed_round"], 2)

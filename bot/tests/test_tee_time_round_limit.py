"""One active tee time per player per round of a tournament.

A submitted scorecard completes the player's seat in a tee time, so that tee
time no longer blocks them from joining another one for the same round.
"""
import os
import tempfile
import unittest

from src import db


class TeeTimeRoundLimitTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.db")
        await db.init_db(self.db_path)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def _tournament(self):
        return await db.create_tournament(
            self.db_path, "guild1", "RL", "stroke", 18,
            "Pebble Beach Golf Links", ",".join(["4"] * 18), None, "admin1",
            start_date="2026-10-03", end_date="2026-10-10",
            rounds=[{"tee_position": "middle"}, {"tee_position": "middle"}],
        )

    async def _tee_time(self, tid, label, round_number):
        return await db.create_tee_time(
            self.db_path, tid, label, "2026-10-04T14:00:00+00:00",
            4, "admin1", None, round_number=round_number,
        )

    async def test_second_tee_time_same_round_conflicts(self):
        tid = await self._tournament()
        await db.register_player(self.db_path, tid, "p1")
        tt1 = await self._tee_time(tid, "Morning", 1)
        tt2 = await self._tee_time(tid, "Afternoon", 1)
        self.assertEqual(await db.join_tee_time(self.db_path, tt1, "p1"), "ok")
        self.assertEqual(
            await db.join_tee_time(self.db_path, tt2, "p1"), "round_conflict"
        )
        conflict = await db.active_tee_time_for_round(
            self.db_path, tid, 1, "p1", exclude_tee_time_id=tt2
        )
        self.assertIsNotNone(conflict)
        self.assertEqual(conflict["id"], tt1)

    async def test_submitted_card_frees_the_round(self):
        tid = await self._tournament()
        await db.register_player(self.db_path, tid, "p1")
        tt1 = await self._tee_time(tid, "Morning", 1)
        tt2 = await self._tee_time(tid, "Afternoon", 1)
        await db.join_tee_time(self.db_path, tt1, "p1")
        # Play the round: submit a card in the first tee time.
        await db.upsert_scorecard(
            self.db_path, tid, "p1", None, tt1, [4] * 18, "verified",
            round_number=1,
        )
        self.assertTrue(
            await db.player_has_submitted(self.db_path, tt1, 1, "p1")
        )
        self.assertIsNone(
            await db.active_tee_time_for_round(self.db_path, tid, 1, "p1")
        )
        self.assertEqual(await db.join_tee_time(self.db_path, tt2, "p1"), "ok")

    async def test_different_rounds_do_not_conflict(self):
        tid = await self._tournament()
        await db.register_player(self.db_path, tid, "p1")
        tt1 = await self._tee_time(tid, "R1", 1)
        tt2 = await self._tee_time(tid, "R2", 2)
        self.assertEqual(await db.join_tee_time(self.db_path, tt1, "p1"), "ok")
        self.assertEqual(await db.join_tee_time(self.db_path, tt2, "p1"), "ok")

    async def test_team_card_counts_as_submitted(self):
        tid = await db.create_tournament(
            self.db_path, "guild1", "RL", "alt_shot", 18,
            "Pebble Beach Golf Links", ",".join(["4"] * 18), None, "admin1",
            start_date="2026-10-03", end_date="2026-10-10",
            rounds=[{"tee_position": "middle"}],
        )
        await db.register_player(self.db_path, tid, "p1")
        team_id = await db.create_team(self.db_path, tid, "Duo", "p1")
        await db.add_team_member(self.db_path, team_id, "p1")
        tt1 = await self._tee_time(tid, "Morning", 1)
        tt2 = await self._tee_time(tid, "Afternoon", 1)
        await db.join_tee_time(self.db_path, tt1, "p1")
        self.assertEqual(
            await db.join_tee_time(self.db_path, tt2, "p1"), "round_conflict"
        )
        # Shared team card covers the member.
        await db.upsert_scorecard(
            self.db_path, tid, None, team_id, tt1, [4] * 18, "verified",
            round_number=1,
        )
        self.assertTrue(
            await db.player_has_submitted(self.db_path, tt1, 1, "p1")
        )
        self.assertEqual(await db.join_tee_time(self.db_path, tt2, "p1"), "ok")

    async def test_is_player_in_tee_time(self):
        tid = await self._tournament()
        tt1 = await self._tee_time(tid, "Morning", 1)
        tt2 = await self._tee_time(tid, "Afternoon", 1)
        await db.register_player(self.db_path, tid, "p1")
        await db.join_tee_time(self.db_path, tt1, "p1")
        self.assertTrue(
            await db.is_player_in_tee_time(self.db_path, tt1, "p1")
        )
        self.assertFalse(
            await db.is_player_in_tee_time(self.db_path, tt2, "p1")
        )
        self.assertFalse(
            await db.is_player_in_tee_time(self.db_path, tt1, "nobody")
        )

    async def test_leave_all_tee_times(self):
        tid = await self._tournament()
        await db.register_player(self.db_path, tid, "p1")
        tt1 = await self._tee_time(tid, "R1", 1)
        tt2 = await self._tee_time(tid, "R2", 2)
        await db.join_tee_time(self.db_path, tt1, "p1")
        await db.join_tee_time(self.db_path, tt2, "p1")
        left = await db.leave_all_tee_times(self.db_path, tid, "p1")
        self.assertEqual(left, 2)
        self.assertFalse(
            await db.is_player_in_tee_time(self.db_path, tt1, "p1")
        )
        self.assertFalse(
            await db.is_player_in_tee_time(self.db_path, tt2, "p1")
        )


if __name__ == "__main__":
    unittest.main()

"""Tests for season points: the points scale, DB helpers, and the
tournament-complete award logic (final_standings_points).

Run with:  .venv/bin/python -m unittest discover -s tests
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    import aiosqlite

    from src import db
    from src import leaderboard_render as lr
    from src import scoring_logic as sl
except ImportError:  # pragma: no cover - envs without optional deps
    aiosqlite = None
    db = None

requires_aiosqlite = unittest.skipIf(
    aiosqlite is None, "aiosqlite not installed"
)


class PointsScaleTest(unittest.TestCase):
    def test_top_ten_scale(self):
        expected = {1: 100, 2: 90, 3: 80, 4: 70, 5: 60,
                    6: 50, 7: 40, 8: 30, 9: 20, 10: 10}
        for pos, pts in expected.items():
            self.assertEqual(sl.points_for_position(pos), pts,
                             f"position {pos}")

    def test_eleven_and_beyond_get_five(self):
        for pos in (11, 12, 25, 100):
            self.assertEqual(sl.points_for_position(pos), 5)

    def test_invalid_position_clamps_to_first(self):
        self.assertEqual(sl.points_for_position(0), 100)
        self.assertEqual(sl.points_for_position(-3), 100)


@requires_aiosqlite
class SeasonDbTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.db")
        await db.init_db(self.db_path)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def _make_tournament(self, name="T1", format="stroke"):
        return await db.create_tournament(
            self.db_path, "guild1", name, format, 9, "Course A", None, None,
            "admin1", start_date="2026-10-03", end_date="2026-10-10",
        )

    async def test_season_lifecycle(self):
        self.assertIsNone(await db.get_active_season(self.db_path, "guild1"))
        sid = await db.create_season(self.db_path, "guild1", "Fall 2026",
                                     "admin1")
        s = await db.get_active_season(self.db_path, "guild1")
        self.assertEqual(s["id"], sid)
        self.assertEqual(s["status"], "active")
        rows = await db.list_seasons(self.db_path, "guild1")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tournament_count"], 0)
        self.assertTrue(await db.complete_season(self.db_path, sid))
        self.assertIsNone(await db.get_active_season(self.db_path, "guild1"))
        # completing twice is a no-op
        self.assertFalse(await db.complete_season(self.db_path, sid))

    async def test_link_tournament_idempotent(self):
        sid = await db.create_season(self.db_path, "guild1", "S1", "admin1")
        tid = await self._make_tournament()
        self.assertTrue(await db.add_tournament_to_season(
            self.db_path, sid, tid))
        self.assertFalse(await db.add_tournament_to_season(
            self.db_path, sid, tid))
        linked = await db.get_season_tournaments(self.db_path, sid)
        self.assertEqual([t["id"] for t in linked], [tid])

    async def test_points_aggregate(self):
        sid = await db.create_season(self.db_path, "guild1", "S1", "admin1")
        t1 = await self._make_tournament("T1")
        t2 = await self._make_tournament("T2")
        await db.add_tournament_to_season(self.db_path, sid, t1)
        await db.add_tournament_to_season(self.db_path, sid, t2)
        # p1 wins T1 (100), finishes 3rd in T2 (80); p2 2nd in T1 (90)
        n = await db.record_season_points(
            self.db_path, sid, t1, [("p1", 1, 100), ("p2", 2, 90)])
        self.assertEqual(n, 2)
        n = await db.record_season_points(
            self.db_path, sid, t2, [("p1", 3, 80)])
        self.assertEqual(n, 1)
        # re-award is idempotent
        n = await db.record_season_points(
            self.db_path, sid, t1, [("p1", 1, 100)])
        self.assertEqual(n, 0)
        standings = await db.get_season_standings(self.db_path, sid)
        self.assertEqual(len(standings), 2)
        self.assertEqual(standings[0]["discord_id"], "p1")
        self.assertEqual(standings[0]["total_points"], 180)
        self.assertEqual(standings[0]["tournaments_played"], 2)
        self.assertEqual(standings[1]["discord_id"], "p2")
        self.assertEqual(standings[1]["total_points"], 90)
        self.assertEqual(standings[1]["tournaments_played"], 1)
        # seasons lookup by tournament
        seasons = await db.get_seasons_for_tournament(self.db_path, t1)
        self.assertEqual([s["id"] for s in seasons], [sid])
        self.assertEqual(
            await db.get_seasons_for_tournament(
                self.db_path, t1, status="completed"), [])

    async def test_leader_baseline_crud(self):
        tid = await self._make_tournament()
        self.assertIsNone(await db.get_leader(self.db_path, tid))
        await db.set_leader(self.db_path, tid, "p1", "00072")
        row = await db.get_leader(self.db_path, tid)
        self.assertEqual(row["leader_key"], "p1")
        self.assertEqual(row["leader_sort"], "00072")
        await db.set_leader(self.db_path, tid, "p2", "00070")
        row = await db.get_leader(self.db_path, tid)
        self.assertEqual(row["leader_key"], "p2")


@requires_aiosqlite
class AwardLogicTest(unittest.IsolatedAsyncioTestCase):
    """final_standings_points: ties share points, teams award every member."""

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.db")
        await db.init_db(self.db_path)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def _stroke_tournament(self, cards: dict[str, list[int]]):
        """cards: {player_id: holes}. All cards verified."""
        tid = await db.create_tournament(
            self.db_path, "guild1", "Stroke Cup", "stroke", 9, "Course A",
            None, None, "admin1",
            start_date="2026-10-03", end_date="2026-10-10")
        for pid, holes in cards.items():
            await db.upsert_scorecard(self.db_path, tid, pid, None, None,
                                      holes, "verified")
        return tid

    async def test_stroke_positions_and_tie_sharing(self):
        # p1 and p2 tie for 1st (both 100 pts), p3 alone in 3rd (80 pts)
        tid = await self._stroke_tournament({
            "p1": [4] * 9,   # 36
            "p2": [4] * 9,   # 36
            "p3": [5] * 9,   # 45
        })
        rows = await lr.final_standings_points(self.db_path, tid)
        by_pid = {r["player_discord_id"]: r for r in rows}
        self.assertEqual(by_pid["p1"]["position"], 1)
        self.assertEqual(by_pid["p1"]["points"], 100)
        self.assertEqual(by_pid["p2"]["position"], 1)
        self.assertEqual(by_pid["p2"]["points"], 100)
        self.assertEqual(by_pid["p3"]["position"], 3)
        self.assertEqual(by_pid["p3"]["points"], 80)

    async def test_stroke_ignores_pending_and_unsubmitted(self):
        tid = await db.create_tournament(
            self.db_path, "guild1", "Stroke Cup", "stroke", 9, "Course A",
            None, None, "admin1",
            start_date="2026-10-03", end_date="2026-10-10")
        await db.upsert_scorecard(self.db_path, tid, "p1", None, None,
                                  [4] * 9, "verified")
        await db.upsert_scorecard(self.db_path, tid, "p2", None, None,
                                  [3] * 9, "pending")  # not ranked
        rows = await lr.final_standings_points(self.db_path, tid)
        self.assertEqual([r["player_discord_id"] for r in rows], ["p1"])
        self.assertEqual(rows[0]["points"], 100)

    async def test_best_ball_awards_every_member(self):
        tid = await db.create_tournament(
            self.db_path, "guild1", "BB Cup", "best_ball", 9, "Course A",
            None, None, "admin1",
            start_date="2026-10-03", end_date="2026-10-10")
        team_id = await db.create_team(self.db_path, tid, "Aces", "admin1")
        await db.add_team_member(self.db_path, team_id, "p1")
        await db.add_team_member(self.db_path, team_id, "p2")
        # p1 shoots 36, p2 shoots 45 -> best ball 36
        await db.upsert_scorecard(self.db_path, tid, "p1", None, None,
                                  [4] * 9, "verified")
        await db.upsert_scorecard(self.db_path, tid, "p2", None, None,
                                  [5] * 9, "verified")
        rows = await lr.final_standings_points(self.db_path, tid)
        by_pid = {r["player_discord_id"]: r for r in rows}
        self.assertEqual(set(by_pid), {"p1", "p2"})
        self.assertEqual(by_pid["p1"]["points"], 100)
        self.assertEqual(by_pid["p2"]["points"], 100)
        self.assertEqual(by_pid["p1"]["position"], 1)

    async def test_match_format_award(self):
        tid = await db.create_tournament(
            self.db_path, "guild1", "Match Cup", "match", 9, "Course A",
            None, None, "admin1",
            start_date="2026-10-03", end_date="2026-10-10")
        m1 = await db.create_match(self.db_path, tid, "p1", "p2", "p1",
                                   winner="player1")
        await db.confirm_match(self.db_path, m1, "player1")
        m2 = await db.create_match(self.db_path, tid, "p1", "p3", "p1",
                                   winner="tie")
        await db.confirm_match(self.db_path, m2, "tie")
        rows = await lr.final_standings_points(self.db_path, tid)
        by_pid = {r["player_discord_id"]: r for r in rows}
        # p1: 1W 0L 1T -> 1st; p3: 0W 0L 1T -> 2nd; p2: 0W 1L 0T -> 3rd
        self.assertEqual(by_pid["p1"]["position"], 1)
        self.assertEqual(by_pid["p1"]["points"], 100)
        self.assertEqual(by_pid["p3"]["position"], 2)
        self.assertEqual(by_pid["p3"]["points"], 90)
        self.assertEqual(by_pid["p2"]["position"], 3)
        self.assertEqual(by_pid["p2"]["points"], 80)

    async def test_get_leader_stroke(self):
        tid = await self._stroke_tournament({"p1": [4] * 9, "p2": [5] * 9})
        leader = await lr.get_leader(self.db_path, tid)
        self.assertEqual(leader["key"], "p1")
        self.assertIn("36", leader["scoreline"])
        self.assertTrue(lr._is_better_leader("stroke", "00045", leader["sort_value"]))
        self.assertFalse(lr._is_better_leader("stroke", "00036", leader["sort_value"]))

    async def test_get_leader_empty(self):
        tid = await db.create_tournament(
            self.db_path, "guild1", "Empty Cup", "stroke", 9, "Course A",
            None, None, "admin1",
            start_date="2026-10-03", end_date="2026-10-10")
        self.assertIsNone(await lr.get_leader(self.db_path, tid))

    async def test_match_leader_comparison(self):
        self.assertTrue(lr._is_better_leader("match", "0001-0000-0000",
                                             "0002-0000-0000"))  # more wins
        self.assertTrue(lr._is_better_leader("match", "0001-0001-0000",
                                             "0001-0000-0000"))  # fewer losses
        self.assertFalse(lr._is_better_leader("match", "0002-0000-0000",
                                              "0001-0005-0003"))


if __name__ == "__main__":
    unittest.main()

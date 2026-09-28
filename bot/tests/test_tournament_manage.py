"""DB tests for tournament management: update_tournament, delete_tournament,
tournament_usage_counts. Needs aiosqlite but no Discord connection.

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
except ImportError:  # pragma: no cover - envs without optional deps
    aiosqlite = None
    db = None

requires_aiosqlite = unittest.skipIf(
    aiosqlite is None, "aiosqlite not installed"
)


@requires_aiosqlite
class TempDbTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.db")
        await db.init_db(self.db_path)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def _make_tournament(self, name="T1"):
        return await db.create_tournament(
            self.db_path, "guild1", name, "stroke", 18, "Course A",
            None, None, "admin1",
            start_date="2026-10-03", end_date="2026-10-10",
        )


@requires_aiosqlite
class TestUpdateTournament(TempDbTest):
    async def test_partial_update(self):
        tid = await self._make_tournament()
        changed = await db.update_tournament(
            self.db_path, tid, name="Renamed", course="Course B")
        self.assertTrue(changed)
        t = await db.get_tournament(self.db_path, tid)
        self.assertEqual(t["name"], "Renamed")
        self.assertEqual(t["course"], "Course B")
        # untouched fields preserved
        self.assertEqual(t["format"], "stroke")
        self.assertEqual(t["start_date"], "2026-10-03")

    async def test_dates_update(self):
        tid = await self._make_tournament()
        changed = await db.update_tournament(
            self.db_path, tid, start_date="2026-11-01", end_date="2026-11-05")
        self.assertTrue(changed)
        t = await db.get_tournament(self.db_path, tid)
        self.assertEqual(t["start_date"], "2026-11-01")
        self.assertEqual(t["end_date"], "2026-11-05")

    async def test_noop_returns_false(self):
        tid = await self._make_tournament()
        self.assertFalse(await db.update_tournament(self.db_path, tid))

    async def test_missing_tournament_returns_false(self):
        self.assertFalse(
            await db.update_tournament(self.db_path, 9999, name="X"))


@requires_aiosqlite
class TestDeleteTournament(TempDbTest):
    async def test_usage_counts(self):
        tid = await self._make_tournament()
        tt = await db.create_tee_time(
            self.db_path, tid, "TT1", "2026-10-03T19:00:00+00:00",
            4, "p1", "chan1")
        await db.join_tee_time(self.db_path, tt, "p1")
        await db.register_player(self.db_path, tid, "p2")
        counts = await db.tournament_usage_counts(self.db_path, tid)
        self.assertEqual(counts["registrations"], 1)
        self.assertEqual(counts["tee_times"], 1)
        self.assertEqual(counts["scorecards"], 0)

    async def test_delete_cascades(self):
        tid = await self._make_tournament()
        tt = await db.create_tee_time(
            self.db_path, tid, "TT1", "2026-10-03T19:00:00+00:00",
            4, "p1", "chan1")
        await db.join_tee_time(self.db_path, tt, "p1")
        await db.register_player(self.db_path, tid, "p2")
        other = await self._make_tournament(name="T2")

        await db.delete_tournament(self.db_path, tid)

        self.assertIsNone(await db.get_tournament(self.db_path, tid))
        self.assertEqual(
            await db.tournament_usage_counts(self.db_path, tid),
            {"registrations": 0, "tee_times": 0, "scorecards": 0})
        self.assertIsNone(await db.get_tee_time(self.db_path, tt))
        self.assertEqual(await db.get_tee_time_players(self.db_path, tt), [])
        self.assertEqual(await db.list_rounds(self.db_path, tid), [])
        # unrelated tournament untouched
        t2 = await db.get_tournament(self.db_path, other)
        self.assertIsNotNone(t2)
        self.assertEqual(t2["name"], "T2")

    async def test_delete_cascades_scorecards(self):
        tid = await self._make_tournament()
        await db.upsert_scorecard(
            self.db_path, tid, "p1", None, None, [4] * 18, "verified",
            submitted_by="p1")
        await db.delete_tournament(self.db_path, tid)
        counts = await db.tournament_usage_counts(self.db_path, tid)
        self.assertEqual(counts["scorecards"], 0)


if __name__ == "__main__":
    unittest.main()

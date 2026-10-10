"""Per-hole score entry timestamps (hole_times_json on scorecards).

Each hole is stamped when its score is entered; correcting a hole
re-stamps it; the final submit preserves live-entry times so the archive
view can show the real pace of the round for score validation.
"""
import json
import os
import tempfile
import unittest

from src import db

PARS_18 = ",".join(["4"] * 18)


def _times(card):
    return json.loads(card["hole_times_json"] or "[]")


class HoleTimesDbTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.db")
        await db.init_db(self.db_path)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def _make(self):
        return await db.create_tournament(
            self.db_path, "guild1", "HT", "stroke", 18,
            "Pebble Beach Golf Links", PARS_18, None, "admin1")

    def _scores(self, filled):
        return [filled.get(i) for i in range(18)]

    async def test_partial_save_stamps_entered_holes(self):
        tid = await self._make()
        cid = await db.save_partial_scorecard(
            self.db_path, tid, "p1", None, None,
            self._scores({0: 4, 1: 3}))
        times = _times(await db.get_scorecard(self.db_path, cid))
        self.assertEqual(len(times), 18)
        self.assertIsNotNone(times[0])
        self.assertIsNotNone(times[1])
        self.assertTrue(all(t is None for t in times[2:]))

    async def test_partial_save_preserves_earlier_stamps(self):
        tid = await self._make()
        cid = await db.save_partial_scorecard(
            self.db_path, tid, "p1", None, None,
            self._scores({0: 4, 1: 3}))
        first = _times(await db.get_scorecard(self.db_path, cid))
        cid2 = await db.save_partial_scorecard(
            self.db_path, tid, "p1", None, None,
            self._scores({0: 4, 1: 3, 2: 5}))
        self.assertEqual(cid, cid2)
        second = _times(await db.get_scorecard(self.db_path, cid))
        # Unchanged holes keep their original stamps; the new hole is
        # stamped at (or after) the earlier ones.
        self.assertEqual(second[0], first[0])
        self.assertEqual(second[1], first[1])
        self.assertIsNotNone(second[2])
        self.assertGreaterEqual(second[2], first[0])

    async def test_correction_restamps_hole(self):
        tid = await self._make()
        cid = await db.save_partial_scorecard(
            self.db_path, tid, "p1", None, None, self._scores({0: 4}))
        before = _times(await db.get_scorecard(self.db_path, cid))[0]
        await db.save_partial_scorecard(
            self.db_path, tid, "p1", None, None, self._scores({0: 5}))
        after = _times(await db.get_scorecard(self.db_path, cid))[0]
        self.assertIsNotNone(before)
        self.assertGreaterEqual(after, before)

    async def test_upsert_preserves_live_stamps(self):
        tid = await self._make()
        cid = await db.save_partial_scorecard(
            self.db_path, tid, "p1", None, None,
            self._scores({0: 4, 1: 3}))
        live = _times(await db.get_scorecard(self.db_path, cid))
        full = [4, 3] + [4] * 16
        cid2 = await db.upsert_scorecard(
            self.db_path, tid, "p1", None, None, full, "verified",
            submitted_by="p1")
        self.assertEqual(cid, cid2)
        final = _times(await db.get_scorecard(self.db_path, cid))
        # Holes entered live keep their stamps through the final submit.
        self.assertEqual(final[0], live[0])
        self.assertEqual(final[1], live[1])
        self.assertTrue(all(t is not None for t in final))

    async def test_upsert_insert_stamps(self):
        tid = await self._make()
        cid = await db.upsert_scorecard(
            self.db_path, tid, "p1", None, None,
            [4] * 18, "verified", submitted_by="p1")
        times = _times(await db.get_scorecard(self.db_path, cid))
        self.assertEqual(len(times), 18)
        self.assertTrue(all(t is not None for t in times))

    async def test_merge_hole_times_unit(self):
        now = "2026-10-10T00:00:00+00:00"
        old_t = ["2026-10-09T20:00:00+00:00", None]
        # Unchanged score keeps its stamp; new score gets stamped.
        self.assertEqual(
            db._merge_hole_times([4, None], old_t, [4, 3], now),
            ["2026-10-09T20:00:00+00:00", now])
        # Changed score re-stamps.
        self.assertEqual(
            db._merge_hole_times([4, 3], old_t, [5, 3], now),
            [now, now])
        # Untouched holes keep stamps (even when incoming is null).
        self.assertEqual(
            db._merge_hole_times([4, 3], old_t, [None, None], now),
            ["2026-10-09T20:00:00+00:00", None])

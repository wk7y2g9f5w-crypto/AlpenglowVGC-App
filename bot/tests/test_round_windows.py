"""Round windows: per-round dates, tee-time round default, witness names."""
import os
import tempfile
import unittest

from src import db
from src import scoring_logic as sl


class RoundWindowDbTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.db")
        await db.init_db(self.db_path)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def _make(self, **kw):
        return await db.create_tournament(
            self.db_path, "guild1", kw.get("name", "RW"), "stroke", 18,
            "Pebble Beach Golf Links", ",".join(["4"] * 18), None, "admin1",
            start_date=kw.get("start_date", "2026-10-03"),
            end_date=kw.get("end_date", "2026-10-10"),
            rounds=kw.get("rounds"),
        )

    async def test_default_split_two_rounds(self):
        tid = await self._make(rounds=[
            {"tee_position": "middle"},
            {"tee_position": "middle"},
        ])
        rounds = await db.list_rounds(self.db_path, tid)
        self.assertEqual(rounds[0]["start_date"], "2026-10-03")
        self.assertEqual(rounds[0]["end_date"], "2026-10-06")
        self.assertEqual(rounds[1]["start_date"], "2026-10-07")
        self.assertEqual(rounds[1]["end_date"], "2026-10-10")

    async def test_default_split_single_round_covers_tournament(self):
        tid = await self._make()
        rounds = await db.list_rounds(self.db_path, tid)
        self.assertEqual(len(rounds), 1)
        self.assertEqual(rounds[0]["start_date"], "2026-10-03")
        self.assertEqual(rounds[0]["end_date"], "2026-10-10")

    async def test_explicit_round_dates_pass_through(self):
        tid = await self._make(rounds=[
            {"tee_position": "middle",
             "start_date": "2026-10-03", "end_date": "2026-10-04"},
            {"tee_position": "middle",
             "start_date": "2026-10-09", "end_date": "2026-10-10"},
        ])
        rounds = await db.list_rounds(self.db_path, tid)
        self.assertEqual((rounds[0]["start_date"], rounds[0]["end_date"]),
                         ("2026-10-03", "2026-10-04"))
        self.assertEqual((rounds[1]["start_date"], rounds[1]["end_date"]),
                         ("2026-10-09", "2026-10-10"))

    async def test_update_round_dates(self):
        tid = await self._make(rounds=[{"tee_position": "middle"},
                                       {"tee_position": "middle"}])
        r = await db.update_round(self.db_path, tid, 2,
                                  start_date="2026-10-08",
                                  end_date="2026-10-09")
        self.assertEqual(r["start_date"], "2026-10-08")
        self.assertEqual(r["end_date"], "2026-10-09")
        # Untouched round keeps its default split.
        r1 = await db.get_round(self.db_path, tid, 1)
        self.assertEqual(r1["start_date"], "2026-10-03")

    async def test_tee_time_defaults_to_round_1(self):
        tid = await self._make()
        tt_id = await db.create_tee_time(
            self.db_path, tid, "Morning", "2026-10-04T14:00:00+00:00",
            4, "admin1", None)
        tt = await db.get_tee_time(self.db_path, tt_id)
        self.assertEqual(tt["round_number"], 1)
        tt_id2 = await db.create_tee_time(
            self.db_path, tid, "Afternoon", "2026-10-08T14:00:00+00:00",
            4, "admin1", None, round_number=2)
        tt2 = await db.get_tee_time(self.db_path, tt_id2)
        self.assertEqual(tt2["round_number"], 2)

    async def test_witness_name_on_scorecard(self):
        tid = await self._make()
        await db.upsert_player(self.db_path, "p1", "P1")
        await db.register_player(self.db_path, tid, "p1")
        tt_id = await db.create_tee_time(
            self.db_path, tid, "T", "2026-10-04T14:00:00+00:00",
            4, "admin1", None)
        await db.join_tee_time(self.db_path, tt_id, "p1")
        card_id = await db.upsert_scorecard(
            self.db_path, tid, "p1", None, tt_id, [4] * 18, "verified",
            submitted_by="p1", witness_name="  Tank  ")
        card = await db.get_scorecard(self.db_path, card_id)
        self.assertEqual(card["witness_name"], "Tank")
        # Updating the card without a witness clears it.
        await db.upsert_scorecard(
            self.db_path, tid, "p1", None, tt_id, [3] * 18, "verified",
            submitted_by="p1")
        card = await db.get_scorecard(self.db_path, card_id)
        self.assertIsNone(card["witness_name"])


class RoundWindowLogicTest(unittest.TestCase):
    def test_round_has_started(self):
        self.assertTrue(sl.round_has_started(None))
        self.assertTrue(sl.round_has_started("2000-01-01"))
        self.assertTrue(sl.round_has_started("not-a-date"))
        self.assertFalse(sl.round_has_started("2999-01-01"))

    def test_round_has_ended(self):
        self.assertFalse(sl.round_has_ended(None))
        self.assertFalse(sl.round_has_ended("not-a-date"))
        self.assertTrue(sl.round_has_ended("2000-01-01"))
        self.assertFalse(sl.round_has_ended("2999-01-01"))
        # Today counts as still open (inclusive end).
        from datetime import datetime, timezone

        self.assertFalse(
            sl.round_has_ended(
                datetime.now(timezone.utc).date().isoformat()
            )
        )

    def test_validate_round_dates_ok(self):
        s, e = sl.validate_round_dates(
            "2026-10-04", "2026-10-05", "2026-10-03", "2026-10-10")
        self.assertEqual((s, e), ("2026-10-04", "2026-10-05"))

    def test_validate_round_dates_blank_is_default(self):
        self.assertEqual(
            sl.validate_round_dates(None, None, "2026-10-03", "2026-10-10"),
            (None, None))

    def test_validate_round_dates_rejects_inverted(self):
        with self.assertRaises(ValueError):
            sl.validate_round_dates(
                "2026-10-05", "2026-10-04", "2026-10-03", "2026-10-10")

    def test_validate_round_dates_rejects_outside_tournament(self):
        with self.assertRaises(ValueError):
            sl.validate_round_dates(
                "2026-10-01", "2026-10-05", "2026-10-03", "2026-10-10")
        with self.assertRaises(ValueError):
            sl.validate_round_dates(
                "2026-10-05", "2026-10-12", "2026-10-03", "2026-10-10")

    def test_split_date_range_single_day(self):
        out = db._split_date_range("2026-10-03", "2026-10-03", 2)
        # 1-day range, 2 rounds: both land on the same day.
        self.assertEqual(out, [("2026-10-03", "2026-10-03"),
                              ("2026-10-03", "2026-10-03")])


if __name__ == "__main__":
    unittest.main()

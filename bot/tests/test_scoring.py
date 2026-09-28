"""Tests for the tap-to-enter scoring flow and the tee-time gate.

Pure-logic tests need nothing but scoring_logic; the submitted_by tests
need aiosqlite (skipped where it's unavailable, like test_db_features).
"""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import scoring_logic as sl

try:
    import aiosqlite

    from src import db
except ImportError:  # pragma: no cover - envs without optional deps
    aiosqlite = None
    db = None

requires_aiosqlite = unittest.skipIf(
    aiosqlite is None, "aiosqlite not installed"
)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


class TestTeeTimePassed(unittest.TestCase):
    def test_past_is_open(self):
        past = _iso(datetime.now(timezone.utc) - timedelta(hours=2))
        self.assertTrue(sl.tee_time_passed(past))

    def test_future_is_locked(self):
        future = _iso(datetime.now(timezone.utc) + timedelta(hours=2))
        self.assertFalse(sl.tee_time_passed(future))

    def test_z_suffix(self):
        past_z = (datetime.now(timezone.utc) - timedelta(hours=1)
                  ).strftime("%Y-%m-%dT%H:%M:%SZ")
        future_z = (datetime.now(timezone.utc) + timedelta(hours=1)
                    ).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.assertTrue(sl.tee_time_passed(past_z))
        self.assertFalse(sl.tee_time_passed(future_z))

    def test_naive_assumed_utc(self):
        # Naive datetimes are interpreted as UTC.
        self.assertTrue(sl.tee_time_passed("2020-01-01T00:00:00"))
        self.assertFalse(sl.tee_time_passed("2999-01-01T00:00:00"))

    def test_garbage_fails_open(self):
        # A data glitch must never brick score entry.
        self.assertTrue(sl.tee_time_passed("not-a-date"))
        self.assertTrue(sl.tee_time_passed(""))
        self.assertTrue(sl.tee_time_passed(None))

    def test_tee_time_unix(self):
        dt = datetime(2026, 10, 3, 19, 0, tzinfo=timezone.utc)
        self.assertEqual(sl.tee_time_unix(_iso(dt)), int(dt.timestamp()))
        self.assertEqual(
            sl.tee_time_unix("2026-10-03T19:00:00Z"), int(dt.timestamp())
        )
        self.assertIsNone(sl.tee_time_unix("garbage"))
        self.assertIsNone(sl.tee_time_unix(""))


class TestScoreButtonScores(unittest.TestCase):
    def test_par_4(self):
        self.assertEqual(sl.score_button_scores(4), [2, 3, 4, 5, 6, 7])

    def test_par_3(self):
        self.assertEqual(sl.score_button_scores(3), [1, 2, 3, 4, 5, 6])

    def test_par_5(self):
        self.assertEqual(sl.score_button_scores(5), [3, 4, 5, 6, 7, 8])

    def test_always_six_legal_scores(self):
        for par in (3, 4, 5, 6):
            labels = sl.score_button_scores(par)
            self.assertEqual(len(labels), 6)
            self.assertTrue(all(1 <= s <= 15 for s in labels))
            self.assertIn(par, labels)  # par itself is always a button


@requires_aiosqlite
class TestSubmittedBy(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.db")
        await db.init_db(self.db_path)

    async def tearDown(self):
        self.tmp.cleanup()

    async def test_upsert_records_submitter(self):
        tid = await db.create_tournament(
            self.db_path, "g1", "Cup", "stroke", 9, "Course", None, None,
            "admin", start_date="2026-10-03", end_date="2026-10-04",
        )
        tt = await db.create_tee_time(
            self.db_path, tid, "TT1", "2026-10-03T19:00:00+00:00", 4,
            "p1", "chan1",
        )
        card_id = await db.upsert_scorecard(
            self.db_path, tid, "player9", None, tt, [4] * 9, "pending",
            submitted_by="player1",
        )
        card = await db.get_scorecard(self.db_path, card_id)
        self.assertEqual(card["submitted_by"], "player1")
        # Re-submit (upsert path) updates the submitter too.
        await db.upsert_scorecard(
            self.db_path, tid, "player9", None, tt, [5] * 9, "pending",
            submitted_by="player2",
        )
        card = await db.get_scorecard(self.db_path, card_id)
        self.assertEqual(card["submitted_by"], "player2")

    async def test_migration_adds_submitted_by(self):
        legacy = os.path.join(self.tmp.name, "legacy.db")
        async with aiosqlite.connect(legacy) as con:
            await con.execute(
                """CREATE TABLE scorecards(
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  tournament_id INTEGER NOT NULL,
                  player_discord_id TEXT,
                  team_id INTEGER,
                  tee_time_id INTEGER,
                  holes_json TEXT NOT NULL,
                  total INTEGER NOT NULL,
                  status TEXT NOT NULL DEFAULT 'pending',
                  submitted_at TEXT NOT NULL,
                  verified_by TEXT
                )"""
            )
            await con.commit()
        await db.init_db(legacy)  # must migrate, not break
        async with aiosqlite.connect(legacy) as con:
            cur = await con.execute("PRAGMA table_info(scorecards)")
            cols = [r[1] for r in await cur.fetchall()]
        self.assertIn("submitted_by", cols)


@requires_aiosqlite
class TestFindScorecard(unittest.IsolatedAsyncioTestCase):
    """find_scorecard must mirror upsert_scorecard's match exactly — it is
    what decides first-submission vs crew-only edit."""

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.db")
        await db.init_db(self.db_path)
        self.tid = await db.create_tournament(
            self.db_path, "g1", "Cup", "stroke", 9, "Course", None, None,
            "admin", start_date="2026-10-03", end_date="2026-10-04",
        )
        self.tt = await db.create_tee_time(
            self.db_path, self.tid, "TT1", "2026-10-03T19:00:00+00:00", 4,
            "p1", "chan1",
        )
        self.tt2 = await db.create_tee_time(
            self.db_path, self.tid, "TT2", "2026-10-03T20:00:00+00:00", 4,
            "p1", "chan1",
        )

    async def tearDown(self):
        self.tmp.cleanup()

    async def test_none_before_first_submission(self):
        found = await db.find_scorecard(
            self.db_path, self.tid, player_discord_id="p9",
            tee_time_id=self.tt, round_number=1)
        self.assertIsNone(found)

    async def test_finds_upserted_card(self):
        card_id = await db.upsert_scorecard(
            self.db_path, self.tid, "p9", None, self.tt, [4] * 9, "pending")
        found = await db.find_scorecard(
            self.db_path, self.tid, player_discord_id="p9",
            tee_time_id=self.tt, round_number=1)
        self.assertIsNotNone(found)
        self.assertEqual(found["id"], card_id)

    async def test_round_specific(self):
        await db.upsert_scorecard(
            self.db_path, self.tid, "p9", None, self.tt, [4] * 9, "pending",
            round_number=1)
        self.assertIsNone(await db.find_scorecard(
            self.db_path, self.tid, player_discord_id="p9",
            tee_time_id=self.tt, round_number=2))
        await db.upsert_scorecard(
            self.db_path, self.tid, "p9", None, self.tt, [5] * 9, "pending",
            round_number=2)
        found = await db.find_scorecard(
            self.db_path, self.tid, player_discord_id="p9",
            tee_time_id=self.tt, round_number=2)
        self.assertEqual(found["total"], 45)

    async def test_tee_time_specific(self):
        # Same player in two tee times: each tee time's card is independent.
        await db.upsert_scorecard(
            self.db_path, self.tid, "p9", None, self.tt, [4] * 9, "pending")
        self.assertIsNone(await db.find_scorecard(
            self.db_path, self.tid, player_discord_id="p9",
            tee_time_id=self.tt2, round_number=1))

    async def test_team_card(self):
        team_id = await db.create_team(
            self.db_path, self.tid, "Aces", "p9")
        card_id = await db.upsert_scorecard(
            self.db_path, self.tid, None, team_id, self.tt, [4] * 9,
            "pending")
        found = await db.find_scorecard(
            self.db_path, self.tid, team_id=team_id,
            tee_time_id=self.tt, round_number=1)
        self.assertIsNotNone(found)
        self.assertEqual(found["id"], card_id)


if __name__ == "__main__":
    unittest.main()

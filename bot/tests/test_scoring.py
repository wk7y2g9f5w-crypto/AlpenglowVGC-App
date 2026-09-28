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
    from src import leaderboard_render as lr
except ImportError:  # pragma: no cover - envs without optional deps
    aiosqlite = None
    db = None
    lr = None

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


@requires_aiosqlite
class TestLiveScoring(unittest.IsolatedAsyncioTestCase):
    """True live scoring: in_progress cards persist hole by hole, appear on
    the live board with thru, and never leak into final standings."""

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.db")
        await db.init_db(self.db_path)

    async def tearDown(self):
        self.tmp.cleanup()

    async def _tourney(self, fmt="stroke", holes=9):
        pars = ",".join(["4"] * holes)
        tid = await db.create_tournament(
            self.db_path, "g1", "Live", fmt, holes, "Course", pars, None,
            "admin", start_date="2026-10-03", end_date="2026-10-04",
        )
        tt = await db.create_tee_time(
            self.db_path, tid, "TT1", "2026-10-03T19:00:00+00:00", 4,
            "p1", "chan1",
        )
        return tid, tt

    async def test_partial_save_creates_in_progress(self):
        tid, tt = await self._tourney()
        card_id = await db.save_partial_scorecard(
            self.db_path, tid, "p1", None, tt, [4, 5, None] + [None] * 6,
            submitted_by="p1",
        )
        card = await db.get_scorecard(self.db_path, card_id)
        self.assertEqual(card["status"], "in_progress")
        self.assertEqual(card["total"], 9)  # only entered holes count
        found = await db.find_scorecard(self.db_path, tid,
                                        player_discord_id="p1", tee_time_id=tt,
                                        round_number=1)
        self.assertIsNotNone(found)
        self.assertEqual(found["id"], card_id)

    async def test_partial_save_merges_holes(self):
        tid, tt = await self._tourney()
        c1 = await db.save_partial_scorecard(
            self.db_path, tid, "p1", None, tt, [4, None] + [None] * 7,
            submitted_by="p1",
        )
        # Second save fills a different hole; the first is kept, not erased.
        c2 = await db.save_partial_scorecard(
            self.db_path, tid, "p1", None, tt, [None, 5] + [None] * 7,
            submitted_by="p1",
        )
        self.assertEqual(c1, c2)
        card = await db.get_scorecard(self.db_path, c1)
        self.assertEqual(card["total"], 9)
        # Explicit replacement of an entered hole works.
        await db.save_partial_scorecard(
            self.db_path, tid, "p1", None, tt, [6] + [None] * 8,
            submitted_by="p1",
        )
        card = await db.get_scorecard(self.db_path, c1)
        self.assertEqual(card["total"], 11)

    async def test_partial_save_preserves_submitted_status(self):
        # A crew correction of a verified card must not flip it to in_progress.
        tid, tt = await self._tourney()
        card_id = await db.upsert_scorecard(
            self.db_path, tid, "p1", None, tt, [4] * 9, "verified")
        await db.save_partial_scorecard(
            self.db_path, tid, "p1", None, tt, [5] + [None] * 8)
        card = await db.get_scorecard(self.db_path, card_id)
        self.assertEqual(card["status"], "verified")
        self.assertEqual(card["total"], 5 + 4 * 8)

    async def test_best_ball_members_keep_separate_cards(self):
        tid, tt = await self._tourney(fmt="best_ball")
        team_id = await db.create_team(self.db_path, tid, "Aces", "p1")
        await db.add_team_member(self.db_path, team_id, "p1")
        await db.add_team_member(self.db_path, team_id, "p2")
        c1 = await db.save_partial_scorecard(
            self.db_path, tid, "p1", team_id, tt, [4] + [None] * 8)
        c2 = await db.save_partial_scorecard(
            self.db_path, tid, "p2", team_id, tt, [5] + [None] * 8)
        self.assertNotEqual(c1, c2)  # no overwrite
        found = await db.find_scorecard(
            self.db_path, tid, player_discord_id="p2", team_id=team_id,
            tee_time_id=tt, round_number=1)
        self.assertIsNotNone(found)
        self.assertEqual(found["id"], c2)

    async def test_live_board_includes_in_progress_with_thru(self):
        tid, tt = await self._tourney()
        await db.save_partial_scorecard(
            self.db_path, tid, "p1", None, tt, [4, 5] + [None] * 7)
        t = await db.get_tournament(self.db_path, tid)
        ranked, _pending = await lr._stroke_ranked(
            self.db_path, t, include_in_progress=True)
        self.assertEqual(len(ranked), 1)
        row = ranked[0]
        self.assertTrue(row["on_course"])
        detail = [d for d in row["rounds"]
                  if d["status"] == "in_progress"]
        self.assertEqual(len(detail), 1)
        self.assertEqual(detail[0]["thru"], 2)
        self.assertEqual(detail[0]["total"], 9)
        # Default (final-standings) ranking excludes the live card.
        ranked_final, _ = await lr._stroke_ranked(self.db_path, t)
        self.assertEqual(len(ranked_final), 0)

    async def test_old_schema_migrates_status_check(self):
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
                  status TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','verified')),
                  submitted_at TEXT NOT NULL,
                  verified_by TEXT
                )"""
            )
            await con.execute(
                "INSERT INTO scorecards (tournament_id, holes_json, total,"
                " status, submitted_at) VALUES (1, '[4,4]', 8, 'verified',"
                " '2026-01-01')"
            )
            await con.commit()
        await db.init_db(legacy)  # must migrate, not lose the row
        async with aiosqlite.connect(legacy) as con:
            cur = await con.execute("SELECT status, total FROM scorecards")
            rows = await cur.fetchall()
        self.assertEqual(rows, [("verified", 8)])
        # The widened check now accepts in_progress.
        async with aiosqlite.connect(legacy) as con:
            await con.execute(
                "INSERT INTO scorecards (tournament_id, holes_json, total,"
                " status, submitted_at) VALUES (1, '[4]', 4, 'in_progress',"
                " '2026-01-01')"
            )
            await con.commit()

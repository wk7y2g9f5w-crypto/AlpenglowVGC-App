"""Multi-round tournament tests: rounds table, round-tagged scorecards,
and aggregated leaderboards (stroke + best ball)."""
import os
import tempfile
import unittest

from src import db
from src import leaderboard_render as lr

PARS_18 = ",".join(["4"] * 18)
ROUNDS_2 = [
    {"tee_position": "back", "pin_position": "black",
     "wind_strength": "severe"},
    {"tee_position": "middle", "pin_position": "white",
     "wind_strength": "moderate"},
]


class MultiRoundDbTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.db")
        await db.init_db(self.db_path)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def _make(self, **kw):
        kw.setdefault("rounds", None)
        return await db.create_tournament(
            self.db_path, "guild1", kw.get("name", "MR"), "stroke", 18,
            "Pebble Beach Golf Links", PARS_18, None, "admin1",
            start_date="2026-10-03", end_date="2026-10-10",
            rounds=kw["rounds"],
        )

    async def test_default_single_round(self):
        tid = await self._make()
        rounds = await db.list_rounds(self.db_path, tid)
        self.assertEqual(len(rounds), 1)
        self.assertEqual(rounds[0]["round_number"], 1)
        self.assertEqual(rounds[0]["tee_position"], "middle")

    async def test_create_with_rounds(self):
        tid = await self._make(rounds=ROUNDS_2)
        rounds = await db.list_rounds(self.db_path, tid)
        self.assertEqual([r["round_number"] for r in rounds], [1, 2])
        self.assertEqual(rounds[0]["tee_position"], "back")
        self.assertEqual(rounds[1]["pin_position"], "white")

    async def test_update_round(self):
        tid = await self._make(rounds=ROUNDS_2)
        r = await db.update_round(self.db_path, tid, 2,
                                  tee_position="front")
        self.assertEqual(r["tee_position"], "front")
        self.assertEqual(r["pin_position"], "white")  # untouched
        self.assertIsNone(await db.update_round(self.db_path, tid, 9))

    async def test_scorecard_round_isolation(self):
        tid = await self._make(rounds=ROUNDS_2)
        c1 = await db.upsert_scorecard(self.db_path, tid, "p1", None, None,
                                       [4] * 18, "verified", round_number=1)
        c2 = await db.upsert_scorecard(self.db_path, tid, "p1", None, None,
                                       [5] * 18, "verified", round_number=2)
        self.assertNotEqual(c1, c2)
        # Re-submitting round 1 updates the same card (no duplicate).
        c1b = await db.upsert_scorecard(self.db_path, tid, "p1", None, None,
                                        [4] * 17 + [5], "verified",
                                        round_number=1)
        self.assertEqual(c1, c1b)
        card = await db.get_latest_player_card(self.db_path, tid, "p1",
                                               round_number=1)
        self.assertEqual(card["total"], 73)
        card2 = await db.get_latest_player_card(self.db_path, tid, "p1",
                                                round_number=2)
        self.assertEqual(card2["total"], 90)

    async def test_stroke_ranked_aggregates_rounds(self):
        tid = await self._make(rounds=ROUNDS_2)
        for pid in ("p1", "p2"):
            await db.register_player(self.db_path, tid, pid)
        # p1: 72 + 90 = 162 (+18). p2: 76 + 76 = 152 (+8) — wins.
        await db.upsert_scorecard(self.db_path, tid, "p1", None, None,
                                  [4] * 18, "verified", round_number=1)
        await db.upsert_scorecard(self.db_path, tid, "p1", None, None,
                                  [5] * 18, "verified", round_number=2)
        await db.upsert_scorecard(self.db_path, tid, "p2", None, None,
                                  [4] * 17 + [8], "verified", round_number=1)
        await db.upsert_scorecard(self.db_path, tid, "p2", None, None,
                                  [4] * 17 + [8], "verified", round_number=2)
        ranked, pending = await lr._stroke_ranked(self.db_path,
                                                  await db.get_tournament(
                                                      self.db_path, tid))
        self.assertEqual([r["player_discord_id"] for r in ranked],
                         ["p2", "p1"])
        self.assertEqual(ranked[0]["total"], 152)
        self.assertEqual(ranked[0]["to_par"], 8)
        self.assertEqual(ranked[0]["rounds_played"], 2)
        self.assertEqual([d["round_number"] for d in ranked[0]["rounds"]],
                         [1, 2])
        self.assertEqual(pending, [])

    async def test_stroke_ranked_partial_rounds(self):
        tid = await self._make(rounds=ROUNDS_2)
        for pid in ("p1", "p2"):
            await db.register_player(self.db_path, tid, pid)
        # p1 only finished round 1 (E); p2 finished both (+18/round).
        await db.upsert_scorecard(self.db_path, tid, "p1", None, None,
                                  [4] * 18, "verified", round_number=1)
        await db.upsert_scorecard(self.db_path, tid, "p2", None, None,
                                  [5] * 18, "verified", round_number=1)
        await db.upsert_scorecard(self.db_path, tid, "p2", None, None,
                                  [5] * 18, "verified", round_number=2)
        ranked, _ = await lr._stroke_ranked(
            self.db_path, await db.get_tournament(self.db_path, tid))
        self.assertEqual([r["player_discord_id"] for r in ranked],
                         ["p1", "p2"])
        self.assertEqual(ranked[0]["rounds_played"], 1)
        self.assertEqual(ranked[1]["rounds_played"], 2)

    async def test_stroke_ranked_pending_player(self):
        tid = await self._make(rounds=ROUNDS_2)
        await db.register_player(self.db_path, tid, "p1")
        await db.upsert_scorecard(self.db_path, tid, "p1", None, None,
                                  [4] * 18, "pending", round_number=1)
        ranked, pending = await lr._stroke_ranked(
            self.db_path, await db.get_tournament(self.db_path, tid))
        self.assertEqual(ranked, [])
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["player_discord_id"], "p1")

    async def test_best_ball_aggregates_per_round(self):
        tid = await db.create_tournament(
            self.db_path, "guild1", "BB", "best_ball", 18,
            "Pebble Beach Golf Links", PARS_18, None, "admin1",
            rounds=ROUNDS_2)
        team = await db.create_team(self.db_path, tid, "Aces", "p1")
        await db.add_team_member(self.db_path, team, "p1")
        await db.add_team_member(self.db_path, team, "p2")
        # Round 1: best ball per hole = 3s (p1) vs 5s (p2) -> 54.
        await db.upsert_scorecard(self.db_path, tid, "p1", None, None,
                                  [3] * 18, "verified", round_number=1)
        await db.upsert_scorecard(self.db_path, tid, "p2", None, None,
                                  [5] * 18, "verified", round_number=1)
        # Round 2: only p1 scored -> 72.
        await db.upsert_scorecard(self.db_path, tid, "p1", None, None,
                                  [4] * 18, "verified", round_number=2)
        rows, scoreless = await lr._team_rows(
            self.db_path, await db.get_tournament(self.db_path, tid))
        self.assertEqual(scoreless, [])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["total"], 126)
        self.assertEqual(rows[0]["rounds_played"], 2)
        self.assertEqual([d["total"] for d in rows[0]["rounds"]], [54, 72])

    async def test_get_leader_and_points_multi_round(self):
        tid = await self._make(rounds=ROUNDS_2)
        for pid in ("p1", "p2"):
            await db.register_player(self.db_path, tid, pid)
        await db.upsert_scorecard(self.db_path, tid, "p1", None, None,
                                  [4] * 18, "verified", round_number=1)
        await db.upsert_scorecard(self.db_path, tid, "p1", None, None,
                                  [5] * 18, "verified", round_number=2)
        await db.upsert_scorecard(self.db_path, tid, "p2", None, None,
                                  [4] * 17 + [8], "verified", round_number=1)
        await db.upsert_scorecard(self.db_path, tid, "p2", None, None,
                                  [4] * 17 + [8], "verified", round_number=2)
        leader = await lr.get_leader(self.db_path, tid)
        self.assertEqual(leader["key"], "p2")
        self.assertIn("152", leader["scoreline"])
        pts = await lr.final_standings_points(self.db_path, tid)
        by_pid = {p["player_discord_id"]: p for p in pts}
        self.assertEqual(by_pid["p2"]["position"], 1)
        self.assertEqual(by_pid["p2"]["points"], 100)
        self.assertEqual(by_pid["p1"]["position"], 2)
        self.assertEqual(by_pid["p1"]["points"], 90)

    async def test_embed_renders_rounds(self):
        tid = await self._make(rounds=ROUNDS_2)
        await db.register_player(self.db_path, tid, "p1")
        await db.upsert_scorecard(self.db_path, tid, "p1", None, None,
                                  [4] * 18, "verified", round_number=1)
        embed = await lr.build_leaderboard_embed(self.db_path, tid)
        self.assertIsNotNone(embed)
        self.assertIn("2 rounds", embed.description)
        self.assertIn("R1:", embed.description)


if __name__ == "__main__":
    unittest.main()

"""Tests for the pinned live leaderboard board in #🏁leaderboard.

Covers: channel-name preference order, set_leaderboard_message round-trip,
post_leaderboard refusing without a channel, refresh/finalize no-ops
without a stored message, and the live embed including in-progress cards.
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
    from src.cogs import common
except ImportError:  # pragma: no cover - envs without optional deps
    aiosqlite = None
    db = None

requires_aiosqlite = unittest.skipIf(
    aiosqlite is None, "aiosqlite not installed"
)


class FakeChannel:
    def __init__(self, name, id):
        self.name = name
        self.id = id


class FakeGuild:
    def __init__(self, channels):
        self.text_channels = channels


class FakeBot:
    def __init__(self, guilds):
        self._guilds = guilds

    def get_guild(self, guild_id):
        return self._guilds.get(str(guild_id))


@requires_aiosqlite
class LeaderboardBoardTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        await db.init_db(self.db_path)
        self.tid = await db.create_tournament(
            self.db_path, "12345", "Board Cup", "stroke", 9, "Course A",
            None, None, "admin1")

    async def asyncTearDown(self):
        try:
            os.remove(self.db_path)
        except OSError:
            pass

    def test_channel_names_emoji_first(self):
        self.assertEqual(common.LEADERBOARD_CHANNEL_NAMES[0], "🏁leaderboard")
        self.assertIn("leaderboard", common.LEADERBOARD_CHANNEL_NAMES)
        self.assertIn("leaderboard", common.DESIGNATED_CHANNELS)

    def test_find_prefers_emoji_name(self):
        guild = FakeGuild([FakeChannel("leaderboard", 1),
                           FakeChannel("🏁leaderboard", 2)])
        bot = FakeBot({"12345": guild})
        ch = common.find_leaderboard_channel(bot, "12345")
        self.assertEqual(ch.id, 2)

    def test_find_plain_fallback(self):
        guild = FakeGuild([FakeChannel("leaderboard", 7)])
        bot = FakeBot({"12345": guild})
        ch = common.find_leaderboard_channel(bot, "12345")
        self.assertEqual(ch.id, 7)

    def test_find_none_when_missing(self):
        bot = FakeBot({"12345": FakeGuild([])})
        self.assertIsNone(common.find_leaderboard_channel(bot, "12345"))

    async def test_set_leaderboard_message_round_trip(self):
        await db.set_leaderboard_message(self.db_path, self.tid, 111, 222)
        t = await db.get_tournament(self.db_path, self.tid)
        self.assertEqual(t["leaderboard_channel_id"], "111")
        self.assertEqual(t["leaderboard_message_id"], "222")

    async def test_post_without_channel_refuses(self):
        bot = FakeBot({"12345": FakeGuild([])})
        ok, reason = await lr.post_leaderboard(
            bot, self.db_path, "guild1", self.tid)
        self.assertFalse(ok)
        self.assertIn("leaderboard", reason)

    async def test_refresh_without_message_noop(self):
        bot = FakeBot({})
        self.assertFalse(
            await lr.refresh_leaderboard(bot, self.db_path, self.tid))

    async def test_finalize_without_message_noop(self):
        bot = FakeBot({})
        self.assertFalse(
            await lr.finalize_leaderboard(bot, self.db_path, self.tid))

    async def test_live_embed_includes_in_progress(self):
        # Partial (in-progress) card shows on the live board.
        await db.save_partial_scorecard(
            self.db_path, self.tid, "p1", None, None, [4, 5] + [None] * 7)
        embed = await lr.build_leaderboard_embed(self.db_path, self.tid)
        self.assertIsNotNone(embed)
        self.assertIn("Live Leaderboard", embed.title)
        body = "\n".join(f.value or "" for f in embed.fields)
        self.assertIn("p1", body)

    async def test_final_embed_excludes_in_progress(self):
        await db.save_partial_scorecard(
            self.db_path, self.tid, "p1", None, None, [4, 5] + [None] * 7)
        embed = await lr.build_leaderboard_embed(
            self.db_path, self.tid, final=True)
        self.assertIsNotNone(embed)
        self.assertIn("Final Standings", embed.title)


if __name__ == "__main__":
    unittest.main()

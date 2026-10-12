"""Tests for /tournament announce support (Cayden 2026-10-12).

The signup announcement posts exactly once at creation; when that post
is missed there was no recovery path. Covers: the announce command's
registration, the signup-channel name lookup (including the skin-tone
emoji variant a phone picker produces, which silently matched nothing),
and post_signup_announcement's gating on tournament status + channel.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    import aiosqlite

    from src import db
    from src.cogs import tournaments as tc
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
        self.sent = []

    async def send(self, **kwargs):
        self.sent.append(kwargs)
        return None


class FakeGuild:
    def __init__(self, channels):
        self.text_channels = channels


class FakeBot:
    def __init__(self, guilds, db_path):
        self._guilds = guilds
        self.db_path = db_path
        self.views = []

    def get_guild(self, guild_id):
        return self._guilds.get(str(guild_id))

    def add_view(self, view):
        self.views.append(view)


@requires_aiosqlite
class AnnounceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        await db.init_db(self.db_path)
        self.tid = await db.create_tournament(
            self.db_path, "12345", "Demo Cup", "alt_shot", 18,
            "Course A", None, None, "admin1")

    async def asyncTearDown(self):
        try:
            os.remove(self.db_path)
        except OSError:
            pass

    def test_announce_command_registered(self):
        names = [c.name for c in tc.Tournaments.tournament.commands]
        self.assertIn("announce", names)

    def test_signup_channel_names_order(self):
        names = tc.SIGNUP_CHANNEL_NAMES
        # Plain emoji name first (✍ + variation selector), then the
        # skin-tone variant (✍ + U+1F3FB), then the bare name.
        self.assertEqual(names[0], "✍️event-signups")
        self.assertEqual(names[1], "✍🏻event-signups")
        self.assertEqual(names[-1], "event-signups")

    def test_signup_channel_lookup_variants(self):
        for chan_name in ("✍️event-signups", "✍🏻event-signups",
                          "event-signups"):
            guild = FakeGuild([FakeChannel(chan_name, 5)])
            bot = FakeBot({"12345": guild}, self.db_path)
            ch = tc.signup_channel(bot, "12345")
            self.assertIsNotNone(ch, chan_name)
            self.assertEqual(ch.id, 5)

    def test_signup_channel_missing(self):
        guild = FakeGuild([FakeChannel("general", 1)])
        bot = FakeBot({"12345": guild}, self.db_path)
        self.assertIsNone(tc.signup_channel(bot, "12345"))

    async def test_post_announcement_happy_path(self):
        channel = FakeChannel("✍🏻event-signups", 9)
        bot = FakeBot({"12345": FakeGuild([channel])}, self.db_path)
        posted = await tc.post_signup_announcement(bot, "12345", self.tid)
        self.assertTrue(posted)
        self.assertEqual(len(channel.sent), 1)
        self.assertEqual(channel.sent[0]["content"], "@everyone")
        self.assertIsNotNone(channel.sent[0]["view"])

    async def test_post_announcement_wrong_status(self):
        await db.set_tournament_status(self.db_path, self.tid,
                                       "in_progress")
        channel = FakeChannel("✍️event-signups", 9)
        bot = FakeBot({"12345": FakeGuild([channel])}, self.db_path)
        posted = await tc.post_signup_announcement(bot, "12345", self.tid)
        self.assertFalse(posted)
        self.assertEqual(channel.sent, [])

    async def test_post_announcement_no_channel(self):
        bot = FakeBot({"12345": FakeGuild([])}, self.db_path)
        posted = await tc.post_signup_announcement(bot, "12345", self.tid)
        self.assertFalse(posted)

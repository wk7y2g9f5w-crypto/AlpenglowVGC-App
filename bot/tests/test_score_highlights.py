"""Tests for score highlight Discord posts (aces, albatrosses, top-3).

Covers post_score_highlight in src/cogs/tournaments.py: embed content per
event type (including to-par on the top-3 lines), the missing-channel skip,
and the missing-tournament skip.

Run with:  ../.venv/bin/python -m pytest -q  (from bot/)
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import discord  # noqa: E402

from src import db  # noqa: E402
from src.cogs import tournaments as tc  # noqa: E402


class FakeChannel:
    def __init__(self, name):
        self.name = name
        self.sent = []

    async def send(self, embed=None, content=None, **kwargs):
        self.sent.append(embed)
        return None


class FakeGuild:
    def __init__(self, channels):
        self.text_channels = channels
        self.id = 12345


class FakeBot:
    def __init__(self, db_path, guild):
        self.db_path = db_path
        self._guild = guild

    def get_guild(self, gid):
        return self._guild if str(gid) == "12345" else None


class TestPostScoreHighlight(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = self.tmp.name
        await db.init_db(self.db_path)
        self.tid = await db.create_tournament(
            self.db_path, "12345", "Test Open", "stroke", 18,
            "Pebble Beach", ",".join(["4"] * 18), "", "999")

    async def asyncTearDown(self):
        os.unlink(self.db_path)

    def _bot(self, *channel_names):
        channels = [FakeChannel(n) for n in channel_names]
        return FakeBot(self.db_path, FakeGuild(channels)), channels

    async def test_top3_posts_with_to_par(self):
        bot, channels = self._bot("tournament-notifications")
        ok = await tc.post_score_highlight(bot, {
            "tournament_id": self.tid, "event_type": "top3",
            "top3_names": ["Cayden", "Kristian"],
            "top3": [{"name": "Cayden", "to_par": 0},
                     {"name": "Kristian", "to_par": -2}],
            "course": "Pebble Beach"})
        self.assertTrue(ok)
        self.assertEqual(len(channels[0].sent), 1)
        embed = channels[0].sent[0]
        self.assertIsInstance(embed, discord.Embed)
        self.assertIn("Top 3", embed.title)
        self.assertIn("🥇 Cayden (E)", embed.description)
        self.assertIn("🥈 Kristian (-2)", embed.description)

    async def test_ace_post(self):
        bot, channels = self._bot("tournament-notifications")
        ok = await tc.post_score_highlight(bot, {
            "tournament_id": self.tid, "event_type": "ace",
            "player_name": "Cayden", "hole": 7, "course": "Pebble"})
        self.assertTrue(ok)
        self.assertEqual(len(channels[0].sent), 1)
        self.assertIn("HOLE-IN-ONE", channels[0].sent[0].title)

    async def test_albatross_post(self):
        bot, channels = self._bot("tournament-notifications")
        ok = await tc.post_score_highlight(bot, {
            "tournament_id": self.tid, "event_type": "albatross",
            "player_name": "Cayden", "hole": 7, "course": "Pebble",
            "under": 3})
        self.assertTrue(ok)
        self.assertIn("ALBATROSS", channels[0].sent[0].title)

    async def test_missing_channel_skips(self):
        bot, _ = self._bot("general")
        ok = await tc.post_score_highlight(bot, {
            "tournament_id": self.tid, "event_type": "top3",
            "top3_names": ["Cayden"], "top3": [{"to_par": 0}]})
        self.assertFalse(ok)

    async def test_missing_tournament_skips(self):
        bot, _ = self._bot("tournament-notifications")
        ok = await tc.post_score_highlight(bot, {
            "tournament_id": 99999, "event_type": "ace",
            "player_name": "Cayden", "hole": 7, "course": "Pebble"})
        self.assertFalse(ok)

    async def test_unknown_event_skips(self):
        bot, channels = self._bot("tournament-notifications")
        ok = await tc.post_score_highlight(bot, {
            "tournament_id": self.tid, "event_type": "bogus"})
        self.assertFalse(ok)
        self.assertEqual(channels[0].sent, [])

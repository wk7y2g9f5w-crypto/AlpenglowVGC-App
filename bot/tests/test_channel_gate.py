"""Tests for the designated-channel gate (src/cogs/common.py).

Commands mapped in DESIGNATED_CHANNELS only run in their channel. Anywhere
else — including a guild that lacks the channel, or a DM — the caller gets
an ephemeral error naming the required channel and the command does not run.
"""
import unittest

from src.cogs import common


class FakeResponse:
    def __init__(self):
        self.sent = []

    async def send_message(self, content, ephemeral=False):
        self.sent.append((content, ephemeral))


class FakeChannel:
    def __init__(self, name, id):
        self.name = name
        self.id = id


class FakeGuild:
    def __init__(self, channels):
        self.text_channels = channels


class FakeInteraction:
    def __init__(self, guild, channel_id):
        self.guild = guild
        self.channel_id = channel_id
        self.response = FakeResponse()


def make_guild(*names):
    return FakeGuild([FakeChannel(n, i + 1) for i, n in enumerate(names)])


class TestDesignatedChannelGate(unittest.IsolatedAsyncioTestCase):
    async def test_ungated_command_allowed_anywhere(self):
        ix = FakeInteraction(make_guild("general"), 1)
        self.assertTrue(await common.require_designated_channel(ix, "whatever"))
        self.assertEqual(ix.response.sent, [])

    async def test_mapping_covers_tee_sheet(self):
        self.assertEqual(common.DESIGNATED_CHANNELS["tee_sheet"], "tee-sheet")

    async def test_allowed_in_designated_channel(self):
        guild = make_guild("general", "tee-sheet")  # tee-sheet has id 2
        ix = FakeInteraction(guild, 2)
        self.assertTrue(await common.require_designated_channel(ix, "tee_sheet"))
        self.assertEqual(ix.response.sent, [])

    async def test_blocked_in_wrong_channel_names_channel(self):
        guild = make_guild("general", "tee-sheet")
        ix = FakeInteraction(guild, 1)
        self.assertFalse(await common.require_designated_channel(ix, "tee_sheet"))
        self.assertEqual(len(ix.response.sent), 1)
        content, ephemeral = ix.response.sent[0]
        self.assertIn("#tee-sheet", content)
        self.assertTrue(ephemeral)

    async def test_blocked_when_guild_lacks_channel(self):
        ix = FakeInteraction(make_guild("general", "random"), 1)
        self.assertFalse(await common.require_designated_channel(ix, "tee_sheet"))
        content, _ = ix.response.sent[0]
        self.assertIn("#tee-sheet", content)

    async def test_blocked_in_dm(self):
        ix = FakeInteraction(None, 99)
        self.assertFalse(await common.require_designated_channel(ix, "tee_sheet"))
        content, _ = ix.response.sent[0]
        self.assertIn("#tee-sheet", content)


if __name__ == "__main__":
    unittest.main()

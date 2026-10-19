"""Refresh-path tests: one board failing must not block the other, and
refresh_boards reports per-board outcomes instead of raising — the
/tee_sheet refresh command relies on that to always answer the admin.
"""
import os
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    import aiosqlite  # noqa: F401
    from src import db
    from src import teesheet as ts_mod
except ImportError:  # pragma: no cover - envs without optional deps
    db = None

requires_deps = unittest.skipIf(db is None, "deps not installed")


class _FakeMessage:
    def __init__(self):
        self.edits = []

    async def edit(self, *, embed=None, view=None):
        self.edits.append((embed, view))


class _FakeChannel:
    def __init__(self, message):
        self._message = message
        self.sent = []

    async def fetch_message(self, message_id):
        return self._message

    async def send(self, *, embed=None, view=None):
        msg = _FakeMessage()
        msg.id = 999
        self.sent.append(msg)
        return msg


@requires_deps
class RefreshBoardsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.db")
        await db.init_db(self.db_path)
        self.message = _FakeMessage()
        self.channel = _FakeChannel(self.message)
        self.bot = types.SimpleNamespace(
            db_path=self.db_path,
            get_channel=lambda cid: self.channel,
            add_view=lambda view: None,
        )

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def _post_boards(self):
        await db.set_board(self.db_path, "g1", ts_mod.REGISTER_KIND,
                           "123", "555")
        await db.set_board(self.db_path, "g1", ts_mod.TEESHEET_KIND,
                           "123", "556")

    async def test_refresh_reports_success_per_board(self):
        await self._post_boards()
        results = await ts_mod.refresh_boards(self.bot, "g1")
        self.assertEqual(results, {ts_mod.REGISTER_KIND: None,
                                   ts_mod.TEESHEET_KIND: None})
        self.assertEqual(len(self.message.edits), 2)

    async def test_one_board_failing_does_not_block_the_other(self):
        await self._post_boards()
        original = ts_mod.build_teesheet_board

        async def boom(bot, guild_id):
            raise RuntimeError("bad tee time data")

        ts_mod.build_teesheet_board = boom
        try:
            results = await ts_mod.refresh_boards(self.bot, "g1")
        finally:
            ts_mod.build_teesheet_board = original
        self.assertIsNone(results[ts_mod.REGISTER_KIND])
        self.assertIn("bad tee time data", results[ts_mod.TEESHEET_KIND])
        self.assertEqual(len(self.message.edits), 1)

    async def test_no_boards_posted_reports_empty(self):
        results = await ts_mod.refresh_boards(self.bot, "g1")
        self.assertEqual(results, {})


if __name__ == "__main__":
    unittest.main()

"""Regression tests for the WAL + busy-timeout connection tuning.

The bot and the API share one SQLite file on Render; with SQLite's
defaults a writer locks everyone else out and simultaneous submits
failed with 'database is locked'. db._connect must put every
connection in WAL mode with a 30s busy timeout.
"""
import asyncio
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    import aiosqlite

    from src import db
except ImportError:  # pragma: no cover - envs without optional deps
    aiosqlite = None
    db = None

requires_aiosqlite = unittest.skipIf(
    aiosqlite is None, "aiosqlite not installed"
)


@requires_aiosqlite
class ConnectTuningTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(self.path)  # let SQLite create it fresh
        await db.init_db(self.path)

    async def asyncTearDown(self):
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(self.path + suffix)
            except FileNotFoundError:
                pass

    async def test_journal_mode_is_wal(self):
        async with db._connect(self.path) as con:
            cur = await con.execute("PRAGMA journal_mode")
            (mode,) = await cur.fetchone()
        self.assertEqual(mode.lower(), "wal")

    async def test_busy_timeout_is_set(self):
        async with db._connect(self.path) as con:
            cur = await con.execute("PRAGMA busy_timeout")
            (ms,) = await cur.fetchone()
        self.assertEqual(ms, db._BUSY_TIMEOUT_MS)

    async def test_busy_timeout_applies_through_public_helpers(self):
        # The shared read/write helpers are what most callers use; they
        # must ride on tuned connections too (WAL persists on the file).
        await db.upsert_player(self.path, "d1", "Player One")
        row = await db.get_player(self.path, "d1")
        self.assertEqual(row["display_name"], "Player One")
        async with aiosqlite.connect(self.path) as con:
            cur = await con.execute("PRAGMA journal_mode")
            (mode,) = await cur.fetchone()
        self.assertEqual(mode.lower(), "wal")

    async def test_concurrent_writers_do_not_lock_each_other_out(self):
        async def write_one(i):
            await db.upsert_player(self.path, f"d{i}", f"Player {i}")

        await asyncio.gather(*(write_one(i) for i in range(12)))
        players = await db.list_players(self.path)
        self.assertEqual(len(players), 12)

    async def test_writer_waits_out_a_held_write_lock(self):
        # Hold the write lock for ~1.5s on one connection; a writer on
        # another connection must wait and succeed (the default 5s
        # timeout already covers this, but the tuned 30s must too),
        # and a reader must not be blocked at all under WAL.
        holder = await db._open(self.path)
        try:
            await holder.execute("BEGIN IMMEDIATE")
            await holder.execute(
                "INSERT INTO players (discord_id, display_name)"
                " VALUES ('holder', 'Holder')"
            )

            async def competing_write():
                await asyncio.sleep(0.2)
                await db.upsert_player(self.path, "late", "Late Writer")

            async def release():
                await asyncio.sleep(1.5)
                await holder.commit()

            await asyncio.gather(competing_write(), release())
        finally:
            await holder.close()
        row = await db.get_player(self.path, "late")
        self.assertIsNotNone(row)


if __name__ == "__main__":
    unittest.main()

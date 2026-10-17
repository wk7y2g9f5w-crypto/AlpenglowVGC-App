"""Unit tests for the guild-standing cache in main.

Every crew/admin gate re-ran the full Discord standing lookup
(member + guild + roles), and /api/players/me runs three gates back
to back — under load the calls rate-limited/timed out and admin
surfaces flickered (the owner, holding Admin, intermittently lost
the Settings admin rows). _fetch_guild_standing now caches
successful standings per user for a few minutes; failures (None)
are never cached.

- Exercises the REAL main._fetch_guild_standing wrapper with a fake
  _fetch_guild_standing_uncached: no network, no Discord calls.
- Standalone TestCase (does NOT import ApiTestCase): importing
  ApiTestCase would make pytest collect the whole API suite again
  under this module.
- unittest only; run with: python -m pytest tests/ -q
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

API_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_DIR))

import main  # noqa: E402

# Captured at import time for the same reason as test_token_cache:
# other suites replace main's fetch_* functions and never restore
# them, but the uncached standing lookup is not one of their seams.
_REAL_UNCACHED = main._fetch_guild_standing_uncached


class FakeUncached:
    """Counts calls; returns a canned standing (or None)."""

    def __init__(self, standing):
        self.standing = standing
        self.calls = 0

    async def __call__(self, discord_id):
        self.calls += 1
        return self.standing


class StandingCacheTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        main._standing_cache.clear()
        self.addCleanup(main._standing_cache.clear)
        self.fake = FakeUncached((True, {"Admin"}))
        patcher = mock.patch.object(
            main, "_fetch_guild_standing_uncached", self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_second_call_is_served_from_cache(self):
        first = await main._fetch_guild_standing("u1")
        second = await main._fetch_guild_standing("u1")
        self.assertEqual(first, (True, {"Admin"}))
        self.assertEqual(second, first)
        self.assertEqual(self.fake.calls, 1)

    async def test_users_are_cached_separately(self):
        await main._fetch_guild_standing("u1")
        await main._fetch_guild_standing("u2")
        self.assertEqual(self.fake.calls, 2)

    async def test_failure_is_never_cached(self):
        self.fake.standing = None
        self.assertIsNone(await main._fetch_guild_standing("u1"))
        self.assertIsNone(await main._fetch_guild_standing("u1"))
        self.assertEqual(self.fake.calls, 2)
        self.assertEqual(main._standing_cache, {})

    async def test_expired_entry_is_refetched(self):
        await main._fetch_guild_standing("u1")
        exp, standing = main._standing_cache["u1"]
        main._standing_cache["u1"] = (0.0, standing)  # long expired
        await main._fetch_guild_standing("u1")
        self.assertEqual(self.fake.calls, 2)

    async def test_invalidate_forces_refetch(self):
        await main._fetch_guild_standing("u1")
        main._invalidate_standing("u1")
        await main._fetch_guild_standing("u1")
        self.assertEqual(self.fake.calls, 2)

    async def test_mutating_a_returned_set_does_not_poison_the_cache(self):
        _priv, names = await main._fetch_guild_standing("u1")
        names.add("Mod")
        _priv2, names2 = await main._fetch_guild_standing("u1")
        self.assertEqual(names2, {"Admin"})
        self.assertEqual(self.fake.calls, 1)


if __name__ == "__main__":
    unittest.main()

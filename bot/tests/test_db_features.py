"""DB-level tests for the new features (join requests, migrations, side
quests, tournament dates). Needs aiosqlite but no Discord connection.

Run with:  .venv/bin/python -m unittest discover -s tests
"""
import os
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    import aiosqlite

    from src import db
    from src import scoring_logic as sl
    from src import teesheet as ts_mod
except ImportError:  # pragma: no cover - envs without optional deps
    aiosqlite = None
    db = None

requires_aiosqlite = unittest.skipIf(
    aiosqlite is None, "aiosqlite not installed"
)

OLD_TOURNAMENTS_DDL = """
CREATE TABLE tournaments(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  guild_id TEXT NOT NULL,
  name TEXT NOT NULL,
  format TEXT NOT NULL CHECK(format IN ('stroke','match','best_ball','alt_shot')),
  holes INTEGER NOT NULL CHECK(holes IN (9,18)),
  course TEXT NOT NULL,
  pars TEXT,
  description TEXT,
  status TEXT NOT NULL DEFAULT 'registration_open'
    CHECK(status IN ('registration_open','in_progress','completed')),
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  leaderboard_channel_id TEXT,
  leaderboard_message_id TEXT
);
"""


@requires_aiosqlite
class TempDbTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.db")
        await db.init_db(self.db_path)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def _make_tournament(self, name="T1", format="stroke", status="registration_open",
                               start="2026-10-03", end="2026-10-10"):
        tid = await db.create_tournament(
            self.db_path, "guild1", name, format, 18, "Course A", None, None,
            "admin1", start_date=start, end_date=end,
        )
        if status != "registration_open":
            await db.set_tournament_status(self.db_path, tid, status)
        return tid

    async def _make_tee_time(self, tid, label="TT1", max_players=4,
                             creator="p1", players=("p1",)):
        tt_id = await db.create_tee_time(
            self.db_path, tid, label, "2026-10-03T19:00:00+00:00",
            max_players, creator, "chan1",
        )
        for p in players:
            await db.join_tee_time(self.db_path, tt_id, p)
        return tt_id


class TestOutbox(TempDbTest):
    async def test_enqueue_poll_ack_roundtrip(self):
        self.assertEqual(await db.poll_outbox(self.db_path), [])
        oid = await db.enqueue_outbox(
            self.db_path, "tournament_created", {"tournament_id": 7})
        self.assertIsInstance(oid, int)
        rows = await db.poll_outbox(self.db_path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "tournament_created")
        self.assertEqual(rows[0]["payload"], {"tournament_id": 7})
        await db.ack_outbox(self.db_path, [rows[0]["id"]])
        self.assertEqual(await db.poll_outbox(self.db_path), [])

    async def test_bad_payload_json_survives(self):
        await db._execute(
            self.db_path,
            "INSERT INTO outbox (kind, payload, created_at) VALUES (?,?,?)",
            ("tournament_created", "not-json{{{", db.utcnow_iso()),
        )
        rows = await db.poll_outbox(self.db_path)
        self.assertEqual(rows[0]["payload"], {})

    async def test_ack_empty_is_noop(self):
        await db.ack_outbox(self.db_path, [])


class TestMigration(TempDbTest):
    async def test_old_db_gets_dates_and_scramble(self):
        # Build a legacy database (pre-dates, pre-scramble) by hand.
        legacy = os.path.join(self.tmp.name, "legacy.db")
        async with aiosqlite.connect(legacy) as con:
            await con.executescript(OLD_TOURNAMENTS_DDL)
            await con.execute(
                "INSERT INTO tournaments (guild_id, name, format, holes, course,"
                " created_by, created_at) VALUES (?,?,?,?,?,?,?)",
                ("g1", "Old Cup", "stroke", 18, "Old Course", "admin",
                 "2026-01-01T00:00:00+00:00"),
            )
            await con.commit()
        await db.init_db(legacy)  # must migrate, not break
        t = await db.get_tournament(legacy, 1)
        self.assertEqual(t["name"], "Old Cup")  # row preserved
        self.assertIn("start_date", t)  # columns added
        self.assertIn("end_date", t)
        self.assertIsNone(t["start_date"])
        # new settings columns get their schema defaults on old rows
        self.assertEqual(t["tee_position"], "middle")
        self.assertEqual(t["pin_position"], "white")
        self.assertEqual(t["wind_strength"], "moderate")
        self.assertEqual(t["green_speed"], "pro")
        # scramble now insertable
        tid = await db.create_tournament(
            legacy, "g1", "Scramble Cup", "scramble", 9, "New Course",
            None, None, "admin", start_date="2026-11-01", end_date="2026-11-02",
        )
        t2 = await db.get_tournament(legacy, tid)
        self.assertEqual(t2["format"], "scramble")
        self.assertEqual(t2["start_date"], "2026-11-01")


class TestJoinRequests(TempDbTest):
    async def test_request_accept_flow(self):
        tid = await self._make_tournament()
        await db.register_player(self.db_path, tid, "p1")
        await db.register_player(self.db_path, tid, "p2")
        tt = await self._make_tee_time(tid)
        self.assertEqual(await db.create_join_request(self.db_path, tt, "p2"), "ok")
        # duplicate request -> pending, not a second row
        self.assertEqual(await db.create_join_request(self.db_path, tt, "p2"), "pending")
        req = await db.get_pending_request(self.db_path, tt, "p2")
        self.assertIsNotNone(req)
        decided = await db.decide_join_request(self.db_path, req["id"], "accepted", "p1")
        self.assertEqual(decided["status"], "accepted")
        self.assertIsNotNone(decided["decided_at"])
        # the cog's accept flow then seats the player (mirrored here)
        self.assertEqual(await db.join_tee_time(self.db_path, tt, "p2"), "ok")
        players = await db.get_tee_time_players(self.db_path, tt)
        self.assertIn("p2", [p["discord_id"] for p in players])
        # requesting again after joining -> already
        self.assertEqual(await db.create_join_request(self.db_path, tt, "p2"), "already")

    async def test_decline_flow(self):
        tid = await self._make_tournament()
        tt = await self._make_tee_time(tid)
        await db.create_join_request(self.db_path, tt, "p2")
        req = await db.get_pending_request(self.db_path, tt, "p2")
        decided = await db.decide_join_request(self.db_path, req["id"], "declined", "p1")
        self.assertEqual(decided["status"], "declined")
        # decided requests can't be decided again
        self.assertIsNone(
            await db.decide_join_request(self.db_path, req["id"], "accepted", "p1")
        )
        # ...but the player may ask again later
        self.assertEqual(await db.create_join_request(self.db_path, tt, "p2"), "ok")

    async def test_full_tee_time_rejects_request(self):
        tid = await self._make_tournament()
        tt = await self._make_tee_time(tid, max_players=1)  # creator fills it
        self.assertEqual(await db.create_join_request(self.db_path, tt, "p2"), "full")

    async def test_accept_when_now_full_declines(self):
        tid = await self._make_tournament()
        tt = await self._make_tee_time(tid, max_players=2)
        await db.create_join_request(self.db_path, tt, "p2")  # pending
        # someone else fills the last spot first
        self.assertEqual(await db.join_tee_time(self.db_path, tt, "p3"), "ok")
        req = await db.get_pending_request(self.db_path, tt, "p2")
        # accept path in the cog: join_tee_time says full -> decide declined
        self.assertEqual(await db.join_tee_time(self.db_path, tt, "p2"), "full")
        decided = await db.decide_join_request(self.db_path, req["id"], "declined", "p1")
        self.assertEqual(decided["status"], "declined")

    async def test_expire_on_tournament_complete(self):
        t1 = await self._make_tournament(name="T1")
        t2 = await self._make_tournament(name="T2")
        tt1 = await self._make_tee_time(t1, label="TT1")
        tt2 = await self._make_tee_time(t2, label="TT2")
        await db.create_join_request(self.db_path, tt1, "p2")
        await db.create_join_request(self.db_path, tt2, "p2")
        expired = await db.expire_join_requests_for_tournament(self.db_path, t1)
        self.assertEqual(expired, 1)
        self.assertIsNone(await db.get_pending_request(self.db_path, tt1, "p2"))
        # other tournament's request untouched
        self.assertIsNotNone(await db.get_pending_request(self.db_path, tt2, "p2"))

    async def test_pending_views_list(self):
        tid = await self._make_tournament()
        tt = await self._make_tee_time(tid)
        await db.create_join_request(self.db_path, tt, "p2")
        pending = await db.list_pending_join_requests(self.db_path)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["tee_time_label"], "TT1")


class TestSideQuests(TempDbTest):
    async def test_log_and_bests(self):
        g = "guild1"
        await db.log_side_quest(
            self.db_path, g, "grp1", "stroke", "Pebble", 9,
            [("Alice", [4] * 9), ("Bob", [5] * 9)], "p1",
        )
        await db.log_side_quest(
            self.db_path, g, "grp2", "stroke", "Pebble", 9,
            [("Alice", [3] * 9)], "p1",
        )
        await db.log_side_quest(
            self.db_path, g, "grp3", "scramble", "Pebble", 9,
            [("Alice", [3] * 9), ("Bob", [3] * 9)], "p1",
        )
        bests = await db.side_quest_bests(self.db_path, g)
        by_key = {(b["format"], b["course"], b["player_name"]): b["best"]
                  for b in bests}
        self.assertEqual(by_key[("stroke", "Pebble", "Alice")], 27)  # improved
        self.assertEqual(by_key[("stroke", "Pebble", "Bob")], 45)
        self.assertEqual(by_key[("scramble", "Pebble", "Alice")], 27)
        # rounds counted
        alice_stroke = [b for b in bests
                        if b["player_name"] == "Alice" and b["format"] == "stroke"][0]
        self.assertEqual(alice_stroke["rounds"], 2)
        # filters
        only_bob = await db.side_quest_bests(self.db_path, g, player_name="bob")
        self.assertTrue(all(b["player_name"] == "Bob" for b in only_bob))

    async def test_recent_groups_rounds(self):
        g = "guild1"
        await db.log_side_quest(
            self.db_path, g, "g1", "scramble", "Augusta", 18,
            [("Alice", [4] * 18), ("Bob", [4] * 18)], "p1",
        )
        await db.log_side_quest(
            self.db_path, g, "g2", "stroke", "Pebble", 9,
            [("Zed", [6] * 9)], "p1",
        )
        recent = await db.side_quest_recent(self.db_path, g, limit=10)
        self.assertEqual(len(recent), 2)
        self.assertEqual(recent[0]["quest_group"], "g2")  # newest first
        self.assertEqual(len(recent[0]["players"]), 1)
        self.assertEqual(len(recent[1]["players"]), 2)


class TestTournamentDates(TempDbTest):
    async def test_create_stores_dates(self):
        tid = await self._make_tournament(start="2026-10-03", end="2026-10-10")
        t = await db.get_tournament(self.db_path, tid)
        self.assertEqual(t["start_date"], "2026-10-03")
        self.assertEqual(t["end_date"], "2026-10-10")

    async def test_list_orders_open_by_start_date(self):
        later = await self._make_tournament(name="Later", start="2026-11-01",
                                            end="2026-11-08")
        sooner = await self._make_tournament(name="Sooner", start="2026-10-03",
                                             end="2026-10-10")
        done = await self._make_tournament(name="Done", start="2026-09-01",
                                           end="2026-09-02", status="completed")
        rows = await db.list_tournaments(self.db_path, "guild1")
        self.assertEqual([r["id"] for r in rows], [sooner, later, done])


class TestTournamentSettings(TempDbTest):
    async def test_settings_round_trip(self):
        tid = await db.create_tournament(
            self.db_path, "guild1", "Windy Cup", "stroke", 18, "Course A",
            None, None, "admin1", start_date="2026-10-03", end_date="2026-10-10",
            tee_position="back", pin_position="black", wind_strength="severe",
            green_speed="pro",
        )
        t = await db.get_tournament(self.db_path, tid)
        self.assertEqual(t["tee_position"], "back")
        self.assertEqual(t["pin_position"], "black")
        self.assertEqual(t["wind_strength"], "severe")
        self.assertEqual(t["green_speed"], "pro")

    async def test_settings_default_when_omitted(self):
        tid = await self._make_tournament()
        t = await db.get_tournament(self.db_path, tid)
        self.assertEqual(t["tee_position"], "middle")
        self.assertEqual(t["pin_position"], "white")
        self.assertEqual(t["wind_strength"], "moderate")
        self.assertEqual(t["green_speed"], "pro")

    async def test_settings_check_rejects_bad_values(self):
        with self.assertRaises(aiosqlite.IntegrityError):
            await db.create_tournament(
                self.db_path, "guild1", "Bad Cup", "stroke", 18, "Course A",
                None, None, "admin1", tee_position="championship",
            )
        with self.assertRaises(aiosqlite.IntegrityError):
            await db.create_tournament(
                self.db_path, "guild1", "Bad Cup 2", "stroke", 18, "Course A",
                None, None, "admin1", green_speed="turbo",
            )

    def test_format_settings(self):
        self.assertEqual(
            sl.format_settings({"tee_position": "front",
                                 "pin_position": "red",
                                 "wind_strength": "low",
                                 "green_speed": "pro"}),
            "Front tees • Red pins • Low wind • Pro greens",
        )


class TestTeeSheetBoards(TempDbTest):
    def _fake_bot(self):
        bot = types.SimpleNamespace()
        bot.db_path = self.db_path
        return bot

    async def test_board_record_round_trip(self):
        self.assertIsNone(await db.get_board(self.db_path, "g1", "register"))
        await db.set_board(self.db_path, "g1", "register", "chan1", "msg1")
        rec = await db.get_board(self.db_path, "g1", "register")
        self.assertEqual(rec["channel_id"], "chan1")
        self.assertEqual(rec["message_id"], "msg1")
        await db.set_board(self.db_path, "g1", "register", "chan2", "msg2")
        rec = await db.get_board(self.db_path, "g1", "register")
        self.assertEqual(rec["channel_id"], "chan2")  # upsert replaces

    async def test_register_board_lists_open_tournaments(self):
        bot = self._fake_bot()
        await self._make_tournament(name="Open Cup", status="registration_open")
        await self._make_tournament(name="Live Cup", status="in_progress")
        embed, tournaments = await ts_mod.build_register_board(bot, "guild1")
        names = [t["name"] for t in tournaments]
        self.assertIn("Open Cup", names)
        self.assertNotIn("Live Cup", names)
        self.assertIn("Open Cup", embed.fields[0].name)

    async def test_teesheet_board_shows_tee_times_grouped(self):
        bot = self._fake_bot()
        tid = await self._make_tournament(name="Open Cup")
        future = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
        tt = await db.create_tee_time(self.db_path, tid, "Saturday AM",
                                      future, 4, "p1", "chan1")
        await db.join_tee_time(self.db_path, tt, "p1")
        embed, sections = await ts_mod.build_teesheet_board(bot, "guild1")
        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0][0]["name"], "Open Cup")
        self.assertEqual(len(sections[0][1]), 1)
        field_names = [f.name for f in embed.fields]
        self.assertTrue(any("Saturday AM" in n for n in field_names))

    async def test_teesheet_board_drops_stale_tee_times(self):
        bot = self._fake_bot()
        tid = await self._make_tournament(name="Open Cup")
        old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        await db.create_tee_time(self.db_path, tid, "Ancient", old, 4,
                                 "p1", "chan1")
        embed, sections = await ts_mod.build_teesheet_board(bot, "guild1")
        self.assertEqual(sections, [])
        self.assertIn("No tee times yet", embed.description)

    def test_board_view_buttons_have_persistent_ids(self):
        view = ts_mod.BoardRegisterView()
        view.rebuild([{"id": 7, "name": "Cup"}])
        btn = view.children[0]
        self.assertEqual(btn.custom_id, "board_reg:7")
        self.assertIsNone(view.timeout)
        tview = ts_mod.BoardTeeSheetView()
        tview.rebuild([({"id": 7, "name": "Cup"},
                        [{"id": 3, "label": "Sat"}])])
        ids = [b.custom_id for b in tview.children]
        self.assertIn("board_ttreq:3", ids)
        self.assertIn("board_ttleave:7", ids)
        self.assertIsNone(tview.timeout)


class TestGolfPlusLinking(TempDbTest):
    async def test_handle_round_trip_and_tag(self):
        await db.upsert_player(self.db_path, "p1", "Cayden")
        await db.set_golfplus_handle(self.db_path, "p1", "CaydenVR")
        row = await db.get_player(self.db_path, "p1")
        self.assertEqual(row["golfplus_handle"], "CaydenVR")
        self.assertEqual(db.display_name_of(row, "p1"), "Cayden (Golf+: CaydenVR)")
        await db.set_golfplus_handle(self.db_path, "p1", None)
        row = await db.get_player(self.db_path, "p1")
        self.assertIsNone(row["golfplus_handle"])
        self.assertEqual(db.display_name_of(row, "p1"), "Cayden")

    def test_display_name_of_without_handle(self):
        self.assertEqual(
            db.display_name_of({"display_name": "Tank", "golfplus_handle": None},
                               "p2"),
            "Tank",
        )
        self.assertEqual(db.display_name_of(None, "p2"), "<@p2>")

    async def test_roster_includes_handle(self):
        await db.upsert_player(self.db_path, "p1", "Cayden")
        await db.set_golfplus_handle(self.db_path, "p1", "CaydenVR")
        tid = await self._make_tournament(name="Cup")
        await db.register_player(self.db_path, tid, "p1")
        roster = await db.get_roster(self.db_path, tid)
        self.assertEqual(
            db.display_name_of(roster[0], roster[0]["discord_id"]),
            "Cayden (Golf+: CaydenVR)",
        )

    def test_clean_handle(self):
        from src.cogs.profile import clean_handle
        self.assertEqual(clean_handle("  CaydenVR  "), "CaydenVR")
        self.assertIsNone(clean_handle("   "))
        self.assertIsNone(clean_handle("x" * 33))
        self.assertEqual(clean_handle("x" * 32), "x" * 32)

    async def test_teesheet_board_shows_handle(self):
        from types import SimpleNamespace
        bot = SimpleNamespace(db_path=self.db_path)
        await db.upsert_player(self.db_path, "p1", "Cayden")
        await db.set_golfplus_handle(self.db_path, "p1", "CaydenVR")
        tid = await self._make_tournament(name="Cup")
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        tt = await db.create_tee_time(self.db_path, tid, "Sat", future, 4,
                                      "p1", "chan1")
        await db.join_tee_time(self.db_path, tt, "p1")
        embed, _ = await ts_mod.build_teesheet_board(bot, "guild1")
        values = " ".join(f.value for f in embed.fields)
        self.assertIn("Cayden (Golf+: CaydenVR)", values)


@requires_aiosqlite
class TimezoneTest(TempDbTest):
    async def test_timezone_migrates_onto_old_players_table(self):
        async with aiosqlite.connect(self.db_path) as con:
            await con.execute("ALTER TABLE players RENAME TO players_old")
            await con.execute(
                "CREATE TABLE players(discord_id TEXT PRIMARY KEY,"
                " display_name TEXT NOT NULL, golfplus_handle TEXT)"
            )
            await con.execute(
                "INSERT INTO players SELECT discord_id, display_name,"
                " golfplus_handle FROM players_old"
            )
            await con.execute("DROP TABLE players_old")
            await con.commit()
        await db._migrate(self.db_path)
        await db.upsert_player(self.db_path, "p1", "Cayden")
        self.assertIsNone(await db.get_timezone(self.db_path, "p1"))
        await db.set_timezone(self.db_path, "p1", "America/Denver")
        self.assertEqual(await db.get_timezone(self.db_path, "p1"),
                         "America/Denver")
        await db.set_timezone(self.db_path, "p1", None)
        self.assertIsNone(await db.get_timezone(self.db_path, "p1"))

    async def test_timezone_unknown_player_is_none(self):
        self.assertIsNone(await db.get_timezone(self.db_path, "ghost"))


class ParseInTzTest(unittest.TestCase):
    def test_denver_summer_mdt(self):
        # 2026-09-26 is MDT (UTC-6).
        from src.cogs import teetimes as tt_mod
        dt = tt_mod.parse_in_tz("2026-09-26", "19:30", "America/Denver")
        self.assertEqual(dt, datetime(2026, 9, 27, 1, 30, tzinfo=timezone.utc))

    def test_denver_winter_mst(self):
        # 2026-01-15 is MST (UTC-7) — DST handled by zoneinfo.
        from src.cogs import teetimes as tt_mod
        dt = tt_mod.parse_in_tz("2026-01-15", "19:30", "America/Denver")
        self.assertEqual(dt, datetime(2026, 1, 16, 2, 30, tzinfo=timezone.utc))

    def test_unset_or_invalid_zone_falls_back_to_utc(self):
        from src.cogs import teetimes as tt_mod
        expected = datetime(2026, 9, 26, 19, 30, tzinfo=timezone.utc)
        self.assertEqual(tt_mod.parse_in_tz("2026-09-26", "19:30", None),
                         expected)
        self.assertEqual(tt_mod.parse_in_tz("2026-09-26", "19:30", "Mars/Olympus"),
                         expected)

    def test_bad_input_raises(self):
        from src.cogs import teetimes as tt_mod
        with self.assertRaises(ValueError):
            tt_mod.parse_in_tz("09-26-2026", "19:30", "UTC")
        with self.assertRaises(ValueError):
            tt_mod.parse_in_tz("2026-09-26", "7:30pm", "UTC")
        with self.assertRaises(ValueError):
            tt_mod.parse_in_tz("2026-02-30", "19:30", "UTC")


@requires_aiosqlite
class RegistrationGateTest(TempDbTest):
    def _fake_interaction(self):
        from types import SimpleNamespace

        class FakeResponse:
            def __init__(self):
                self.sent = []

            async def send_message(self, content, **kwargs):
                self.sent.append(content)

        class FakeUser:
            id = "newbie1"
            display_name = "Newbie"

        ix = SimpleNamespace()
        ix.client = SimpleNamespace(db_path=self.db_path)
        ix.user = FakeUser()
        ix.response = FakeResponse()
        return ix

    async def test_gate_blocks_without_timezone(self):
        from src.cogs import registration as reg_mod
        ix = self._fake_interaction()
        self.assertFalse(await reg_mod.require_timezone(ix))
        self.assertTrue(any("/set_timezone" in m for m in ix.response.sent))
        # Player row was still created (so /setup can update it).
        self.assertIsNotNone(await db.get_player(self.db_path, "newbie1"))

    async def test_gate_passes_with_timezone(self):
        from src.cogs import registration as reg_mod
        await db.upsert_player(self.db_path, "newbie1", "Newbie")
        await db.set_timezone(self.db_path, "newbie1", "America/Denver")
        ix = self._fake_interaction()
        self.assertTrue(await reg_mod.require_timezone(ix))
        self.assertEqual(ix.response.sent, [])


class SetupChecklistTest(unittest.TestCase):
    def test_empty_checklist(self):
        from src.cogs import onboarding as ob
        txt = ob.setup_checklist_text(None, None)
        self.assertIn("⬜", txt)
        self.assertNotIn("✅", txt)
        self.assertIn("finish setup", txt)

    def test_complete_checklist(self):
        from src.cogs import onboarding as ob
        txt = ob.setup_checklist_text("America/Denver", "CaydenVR")
        self.assertIn("✅", txt)
        self.assertNotIn("⬜", txt)
        self.assertIn("America/Denver", txt)
        self.assertIn("CaydenVR", txt)
        self.assertIn("#⛳️tee-times", txt)

    def test_timezone_only_still_ready(self):
        from src.cogs import onboarding as ob
        txt = ob.setup_checklist_text("UTC", None)
        self.assertIn("✅ **Timezone:** UTC", txt)
        self.assertIn("⬜ **Golf+ username:**", txt)
        self.assertIn("#⛳️tee-times", txt)  # handle is optional


class FormatTeeTimeLocalTest(unittest.TestCase):
    def test_denver_viewer(self):
        from src.cogs import common as common_mod
        # 2026-09-27 01:30 UTC = Sep 26, 7:30 PM MDT.
        self.assertEqual(
            common_mod.format_tee_time_local("2026-09-27T01:30:00+00:00",
                                             "America/Denver"),
            "Sep 26, 7:30 PM MDT")

    def test_utc_default_for_unset_or_bad_zone(self):
        from src.cogs import common as common_mod
        self.assertEqual(
            common_mod.format_tee_time_local("2026-09-27T01:30:00+00:00", None),
            "Sep 27, 1:30 AM UTC")
        self.assertEqual(
            common_mod.format_tee_time_local("2026-09-27T01:30:00+00:00",
                                             "Mars/Olympus"),
            "Sep 27, 1:30 AM UTC")

    def test_unparseable_returns_empty(self):
        from src.cogs import common as common_mod
        self.assertEqual(common_mod.format_tee_time_local(None, "UTC"), "")
        self.assertEqual(common_mod.format_tee_time_local("not-a-time", "UTC"), "")


@requires_aiosqlite
class TestDeletePlayerData(TempDbTest):
    """Account deletion: db.delete_player_data wipes the user's rows,
    leaves shared history, and db.list_players orders by display name."""

    async def test_list_players_ordered(self):
        await db.upsert_player(self.db_path, "u1", "Zed")
        await db.upsert_player(self.db_path, "u2", "Amy")
        rows = await db.list_players(self.db_path)
        self.assertEqual([r["discord_id"] for r in rows], ["u2", "u1"])
        self.assertEqual(rows[0]["display_name"], "Amy")

    async def test_delete_player_data(self):
        tid = await self._make_tournament()
        sid = await db.create_season(self.db_path, "guild1", "S1", "admin1")
        tt_id = await self._make_tee_time(tid, players=("victim", "other"))
        await db.upsert_player(self.db_path, "victim", "Victim")
        await db.upsert_player(self.db_path, "other", "Other")
        await db.register_device(self.db_path, "victim", "tok-victim", "ios")
        await db.register_device(self.db_path, "other", "tok-other", "ios")
        await db.set_notification_prefs(
            self.db_path, "victim",
            {"tournament_starts": False, "round_starts": True, "ace": True,
             "albatross": True, "top3_changes": True})
        await db.enqueue_push(self.db_path, "victim", "t", "b")
        await db.register_player(self.db_path, tid, "victim")
        await db.record_season_points(
            self.db_path, sid, tid, [("victim", 1, 10), ("other", 2, 7)])
        # Shared altshot team row: victim is player1, other also on the team.
        await db._execute(
            self.db_path,
            "INSERT INTO altshot_teams (id, tee_time_id, player1_discord_id,"
            " created_at) VALUES (?,?,?,?)",
            ("alt-1", "tt-1", "victim", "2026-10-02T00:00:00"),
        )
        await db._execute(
            self.db_path,
            "INSERT INTO altshot_team_members (id, team_id, discord_id, name,"
            " created_at) VALUES (?,?,?,?,?)",
            ("altm-1", "alt-1", "victim", "Victim", "2026-10-02T00:00:00"),
        )
        await db._execute(
            self.db_path,
            "INSERT INTO matchplay_records (id, format, discord_id,"
            " player_name) VALUES (?,?,?,?)",
            ("mpr-1", "single", "victim", "Victim"),
        )

        result = await db.delete_player_data(self.db_path, "victim")
        self.assertEqual(result["discord_id"], "victim")
        self.assertGreater(result["deleted"]["players"], 0)

        for table, col in [
            ("players", "discord_id"), ("devices", "discord_id"),
            ("notification_prefs", "discord_id"), ("push_outbox", "discord_id"),
            ("registrations", "player_discord_id"),
            ("tee_time_players", "player_discord_id"),
            ("season_points", "player_discord_id"),
            ("altshot_team_members", "discord_id"),
            ("matchplay_records", "discord_id"),
        ]:
            rows = await db._fetchall(
                self.db_path, f"SELECT * FROM {table} WHERE {col} = ?",
                ("victim",))
            self.assertEqual(rows, [], f"{table} still has victim rows")

        # Other player's rows survive.
        other = await db._fetchone(
            self.db_path, "SELECT * FROM devices WHERE discord_id = ?",
            ("other",))
        self.assertIsNotNone(other)
        pts = await db._fetchone(
            self.db_path,
            "SELECT * FROM season_points WHERE player_discord_id = ?",
            ("other",))
        self.assertIsNotNone(pts)
        # Shared team row survives with player1 nulled.
        team = await db._fetchone(
            self.db_path, "SELECT * FROM altshot_teams WHERE id = 'alt-1'")
        self.assertIsNotNone(team)
        self.assertIsNone(team["player1_discord_id"])
        # Shared history intact: tournament + tee time still there.
        self.assertIsNotNone(await db.get_tournament(self.db_path, tid))
        self.assertIsNotNone(await db.get_tee_time(self.db_path, tt_id))


OLD_PLAYERS_DDL = """
CREATE TABLE players(
  discord_id TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  golfplus_handle TEXT,
  timezone TEXT
);
"""


class TestHoleShots(TempDbTest):
    async def _card(self, uid="p1"):
        await db.upsert_player(self.db_path, uid, "Player")
        tid = await db.create_tournament(
            self.db_path, "g1", "Cup", "stroke", 18, "Course",
            ",".join(["4"] * 18), None, uid)
        return await db._execute(
            self.db_path,
            "INSERT INTO scorecards (tournament_id, player_discord_id,"
            " holes_json, total, status, submitted_at)"
            " VALUES (?,?,?,?,?,?)",
            (tid, uid, "[4,4,4,4,4,4,4,4,4,4,4,4,4,4,4,4,4,4]", 72,
             "verified", "2026-10-02T00:00:00"))

    async def test_set_get_replace(self):
        cid = (await self._card())[0]
        n = await db.set_hole_shots(self.db_path, cid, 1, [
            {"x": 0.5, "y": 0.9, "lie": "fairway", "holed": False},
            {"x": 0.5, "y": 0.1, "lie": "green", "holed": True},
        ])
        self.assertEqual(n, 2)
        shots = await db.get_hole_shots(self.db_path, cid, 1)
        self.assertEqual([s["seq"] for s in shots], [1, 2])
        self.assertEqual(shots[0]["lie"], "fairway")
        self.assertEqual(shots[1]["holed"], 1)
        # Replace wholesale.
        await db.set_hole_shots(self.db_path, cid, 1, [
            {"x": 0.1, "y": 0.1, "lie": "rough", "holed": False}])
        shots = await db.get_hole_shots(self.db_path, cid, 1)
        self.assertEqual(len(shots), 1)
        self.assertEqual(shots[0]["lie"], "rough")
        # Other holes untouched; empty hole returns [].
        self.assertEqual(await db.get_hole_shots(self.db_path, cid, 2), [])

    async def test_get_shots_for_scorecards_groups(self):
        c1 = (await self._card("p1"))[0]
        c2 = (await self._card("p2"))[0]
        await db.set_hole_shots(self.db_path, c1, 1, [
            {"x": 0.5, "y": 0.5, "lie": "fairway", "holed": False}])
        await db.set_hole_shots(self.db_path, c2, 9, [
            {"x": 0.2, "y": 0.2, "lie": "sand", "holed": False}])
        grouped = await db.get_shots_for_scorecards(self.db_path, [c1, c2])
        self.assertEqual(set(grouped), {c1, c2})
        self.assertEqual(list(grouped[c1]), [1])
        self.assertEqual(list(grouped[c2]), [9])
        self.assertEqual(await db.get_shots_for_scorecards(self.db_path, []),
                         {})

    async def test_stats_private_round_trip(self):
        await db.upsert_player(self.db_path, "p1", "Player")
        row = await db.get_player(self.db_path, "p1")
        self.assertEqual(row["stats_private"], 0)
        await db.set_stats_private(self.db_path, "p1", True)
        row = await db.get_player(self.db_path, "p1")
        self.assertEqual(row["stats_private"], 1)

    async def test_old_db_gets_stats_private(self):
        legacy = os.path.join(self.tmp.name, "legacy.db")
        async with aiosqlite.connect(legacy) as con:
            await con.executescript(OLD_PLAYERS_DDL)
            await con.execute(
                "INSERT INTO players (discord_id, display_name)"
                " VALUES (?,?)", ("p1", "Old Player"))
            await con.commit()
        await db.init_db(legacy)  # must migrate, not break
        row = await db.get_player(legacy, "p1")
        self.assertEqual(row["display_name"], "Old Player")
        self.assertEqual(row["stats_private"], 0)

    async def test_delete_player_data_removes_shots(self):
        cid = (await self._card("p1"))[0]
        await db.set_hole_shots(self.db_path, cid, 1, [
            {"x": 0.5, "y": 0.5, "lie": "fairway", "holed": False}])
        await db.delete_player_data(self.db_path, "p1")
        self.assertEqual(
            await db.get_hole_shots(self.db_path, cid, 1), [])


if __name__ == "__main__":
    unittest.main()

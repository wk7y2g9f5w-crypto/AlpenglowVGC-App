"""Unit tests for shot-by-shot tracking + honest stats + handicap.

- PUT/GET /api/scorecards/{id}/holes/{hole}/shots (auth + validation).
- GET /api/players/{key}/stats: FIR/GIR/putts/up-down/sand math on a
  hand-computed fixture, WHS-lite handicap, stats_private gating.
- PATCH /api/players/me {stats_private}.
"""
import json
import sys
import unittest
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_DIR))

import main  # noqa: E402
from main import app  # noqa: E402

BOT_DIR = str(Path.home() / "workspace" / "golf-tournament-bot")
if BOT_DIR not in sys.path:
    sys.path.insert(0, BOT_DIR)
from src import db  # noqa: E402
from test_api import ApiTestCase, GUILD, PARS_18, run  # noqa: E402

PARS_4_18 = ",".join(["4"] * 18)


def _shots(*specs):
    """Build a shots payload from (x, y, lie[, holed]) tuples."""
    out = []
    for s in specs:
        d = {"x": s[0], "y": s[1], "lie": s[2]}
        if len(s) > 3:
            d["holed"] = s[3]
        out.append(d)
    return {"shots": out}


class ShotTrackingTests:
    """Mixin: no TestCase base, so pytest collects it only once via the subclass below."""
    def _make_card(self, uid="123", holes=None, total=None, status="verified",
                   tid=None):
        holes = holes if holes is not None else [4] * 18
        total = total if total is not None else sum(holes)
        async def ex(sql, params):
            async with __import__("aiosqlite").connect(self.db_path) as con:
                cur = await con.execute(sql, params)
                await con.commit()
                return cur.lastrowid
        return run(ex(
            "INSERT INTO scorecards (tournament_id, round_number,"
            " player_discord_id, holes_json, total, status, submitted_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (tid or self.t_open, 1, uid, json.dumps(holes), total, status,
             "2026-10-02T00:00:00")))

    def _crew(self, value):
        async def fake(discord_id):
            return value
        main.fetch_crew_status = fake

    # -- PUT / GET round trip -------------------------------------------
    def test_put_get_shots_roundtrip(self):
        cid = self._make_card()
        body = _shots((0.5, 0.9, "fairway"), (0.5, 0.2, "green"),
                      (0.5, 0.1, "green", True))
        r = self.client.put(f"/api/scorecards/{cid}/holes/1/shots",
                            headers=self.h("123"), json=body)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["shots"], 3)
        r = self.client.get(f"/api/scorecards/{cid}/holes/1/shots",
                            headers=self.h("123"))
        self.assertEqual(r.status_code, 200, r.text)
        shots = r.json()["shots"]
        self.assertEqual([s["seq"] for s in shots], [1, 2, 3])
        self.assertEqual(shots[0]["lie"], "fairway")
        self.assertTrue(shots[2]["holed"])
        self.assertFalse(shots[0]["holed"])
        self.assertAlmostEqual(shots[1]["x"], 0.5)

    def test_put_shots_replaces(self):
        cid = self._make_card()
        self.client.put(f"/api/scorecards/{cid}/holes/2/shots",
                        headers=self.h("123"),
                        json=_shots((0.1, 0.1, "rough")))
        r = self.client.put(
            f"/api/scorecards/{cid}/holes/2/shots", headers=self.h("123"),
            json=_shots((0.2, 0.2, "fairway"), (0.3, 0.3, "green", True)))
        self.assertEqual(r.json()["shots"], 2)
        r = self.client.get(f"/api/scorecards/{cid}/holes/2/shots",
                            headers=self.h("123"))
        self.assertEqual(len(r.json()["shots"]), 2)

    def test_put_shots_clears_with_empty_list(self):
        cid = self._make_card()
        self.client.put(f"/api/scorecards/{cid}/holes/3/shots",
                        headers=self.h("123"),
                        json=_shots((0.1, 0.1, "fairway")))
        r = self.client.put(f"/api/scorecards/{cid}/holes/3/shots",
                            headers=self.h("123"), json={"shots": []})
        self.assertEqual(r.json()["shots"], 0)
        r = self.client.get(f"/api/scorecards/{cid}/holes/3/shots",
                            headers=self.h("123"))
        self.assertEqual(r.json()["shots"], [])

    # -- auth ------------------------------------------------------------
    def test_put_shots_401_no_auth(self):
        cid = self._make_card()
        r = self.client.put(f"/api/scorecards/{cid}/holes/1/shots",
                            json=_shots((0.5, 0.5, "fairway")))
        self.assertEqual(r.status_code, 401)

    def test_put_shots_403_non_owner(self):
        cid = self._make_card(uid="123")
        r = self.client.put(f"/api/scorecards/{cid}/holes/1/shots",
                            headers=self.h("456"),
                            json=_shots((0.5, 0.5, "fairway")))
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["code"], "not_card_owner")

    def test_get_shots_403_non_owner(self):
        cid = self._make_card(uid="123")
        r = self.client.get(f"/api/scorecards/{cid}/holes/1/shots",
                            headers=self.h("456"))
        self.assertEqual(r.status_code, 403)

    def test_put_shots_crew_allowed(self):
        cid = self._make_card(uid="123")
        self._crew(True)
        try:
            r = self.client.put(
                f"/api/scorecards/{cid}/holes/1/shots", headers=self.h("999"),
                json=_shots((0.5, 0.5, "fairway")))
            self.assertEqual(r.status_code, 200, r.text)
        finally:
            self._crew(False)

    def test_put_shots_404_unknown_card(self):
        r = self.client.put("/api/scorecards/99999/holes/1/shots",
                            headers=self.h("123"),
                            json=_shots((0.5, 0.5, "fairway")))
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["code"], "scorecard_not_found")

    # -- validation -------------------------------------------------------
    def test_put_shots_422_hole_out_of_range(self):
        cid = self._make_card()
        for bad in (0, 19):
            r = self.client.put(
                f"/api/scorecards/{cid}/holes/{bad}/shots",
                headers=self.h("123"), json=_shots((0.5, 0.5, "fairway")))
            self.assertEqual(r.status_code, 422, bad)

    def test_put_shots_422_bad_lie(self):
        cid = self._make_card()
        r = self.client.put(f"/api/scorecards/{cid}/holes/1/shots",
                            headers=self.h("123"),
                            json=_shots((0.5, 0.5, "bunker")))
        self.assertEqual(r.status_code, 422)

    def test_put_shots_422_bad_coord(self):
        cid = self._make_card()
        r = self.client.put(f"/api/scorecards/{cid}/holes/1/shots",
                            headers=self.h("123"),
                            json=_shots((1.5, 0.5, "fairway")))
        self.assertEqual(r.status_code, 422)

    def test_put_shots_422_holed_not_last(self):
        cid = self._make_card()
        r = self.client.put(
            f"/api/scorecards/{cid}/holes/1/shots", headers=self.h("123"),
            json=_shots((0.5, 0.5, "green", True), (0.5, 0.4, "green")))
        self.assertEqual(r.status_code, 422)

    def test_put_shots_422_two_holed(self):
        cid = self._make_card()
        r = self.client.put(
            f"/api/scorecards/{cid}/holes/1/shots", headers=self.h("123"),
            json=_shots((0.5, 0.5, "green", True), (0.5, 0.4, "green", True)))
        self.assertEqual(r.status_code, 422)


def _holes_for(total):
    """18 hole scores summing to total (4s, remainder spread across holes)."""
    holes = [4] * 18
    diff = total - 72
    i = 0
    while diff != 0:
        step = 1 if diff > 0 else -1
        holes[i % 18] += step
        diff -= step
        i += 1
    return holes


class ShotStatsTestCase(ApiTestCase, ShotTrackingTests):
    """Hand-computed fixture: all pars (par 72, total 72), shots on holes 1-4.

    H1: F, G, G(holed)      -> FIR yes, GIR yes, 1 putt
    H2: R, G, G, G(holed)   -> FIR no,  GIR yes, 2 putts
    H3: F, R, G, G(holed)   -> FIR yes, GIR no (green at seq 3 > 2), 1 putt,
                               no GIR + par -> up&down yes
    H4: S, S, G, G, G(holed)-> FIR no,  GIR no, 2 putts, up&down yes,
                               sand + no GIR + par -> sand save yes
    Expected: FIR 50.0, GIR 50.0, putts/GIR 1.5, up&down 100.0, sand 100.0,
    rounds_tracked 1, fully 0 -> putts/round None, handicap None (1 round).
    """

    def _card_with_shots(self, uid="123"):
        run(db.upsert_player(self.db_path, uid, f"User{uid}"))
        async def ex(sql, params):
            import aiosqlite
            async with aiosqlite.connect(self.db_path) as con:
                cur = await con.execute(sql, params)
                await con.commit()
                return cur.lastrowid
        cid = run(ex(
            "INSERT INTO scorecards (tournament_id, round_number,"
            " player_discord_id, holes_json, total, status, submitted_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (self.t_open, 1, uid, json.dumps([4] * 18), 72, "verified",
             "2026-10-02T00:00:00")))
        holes = {
            1: [(0.5, 0.9, "fairway"), (0.5, 0.2, "green"),
                (0.5, 0.1, "green", True)],
            2: [(0.4, 0.9, "rough"), (0.5, 0.2, "green"),
                (0.5, 0.15, "green"), (0.5, 0.1, "green", True)],
            3: [(0.5, 0.9, "fairway"), (0.6, 0.5, "rough"),
                (0.5, 0.2, "green"), (0.5, 0.1, "green", True)],
            4: [(0.3, 0.9, "sand"), (0.35, 0.7, "sand"),
                (0.5, 0.2, "green"), (0.5, 0.15, "green"),
                (0.5, 0.1, "green", True)],
        }
        for hole, specs in holes.items():
            run(db.set_hole_shots(
                self.db_path, cid, hole,
                [{"x": x, "y": y, "lie": lie, "holed": bool(rest[0]) if rest else False}
                 for x, y, lie, *rest in specs]))
        return cid

    def _stats(self, key, as_uid="123"):
        r = self.client.get(f"/api/players/{key}/stats",
                            headers=self.h(as_uid))
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_stats_math(self):
        self._card_with_shots()
        s = self._stats("123")
        self.assertEqual(s["rounds_tracked"], 1)
        self.assertEqual(s["rounds_fully_tracked"], 0)
        self.assertEqual(s["fairways_hit_pct"], 50.0)
        self.assertEqual(s["gir_pct"], 50.0)
        self.assertEqual(s["putts_per_gir"], 1.5)
        self.assertIsNone(s["putts_per_round"])
        self.assertEqual(s["up_down_pct"], 100.0)
        self.assertEqual(s["sand_save_pct"], 100.0)
        self.assertIsNone(s["handicap_index"])
        self.assertEqual(s["player"]["discord_id"], "123")

    def test_stats_no_cards(self):
        s = self._stats("123")
        self.assertEqual(s["rounds_tracked"], 0)
        self.assertIsNone(s["fairways_hit_pct"])
        self.assertIsNone(s["gir_pct"])
        self.assertIsNone(s["handicap_index"])

    def test_stats_404_unknown_player(self):
        r = self.client.get("/api/players/nonexistent/stats",
                            headers=self.h("123"))
        self.assertEqual(r.status_code, 404)

    def test_stats_ignores_in_progress_cards(self):
        async def ex(sql, params):
            import aiosqlite
            async with aiosqlite.connect(self.db_path) as con:
                await con.execute(sql, params)
                await con.commit()
        run(ex(
            "INSERT INTO scorecards (tournament_id, round_number,"
            " player_discord_id, holes_json, total, status, submitted_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (self.t_open, 1, "123", json.dumps([4] * 18), 72, "in_progress",
             "2026-10-02T00:00:00")))
        s = self._stats("123")
        self.assertEqual(s["rounds_tracked"], 0)

    def test_handicap_best_of_rounds(self):
        # 3 rounds: differentials -2, 0, +4 -> best 3 avg 2/3 * 0.96 = 0.6.
        async def ex(sql, params):
            import aiosqlite
            async with aiosqlite.connect(self.db_path) as con:
                await con.execute(sql, params)
                await con.commit()
        for total, day in ((70, "01"), (72, "02"), (76, "03")):
            run(ex(
                "INSERT INTO scorecards (tournament_id, round_number,"
                " player_discord_id, holes_json, total, status, submitted_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (self.t_open, 1, "123", json.dumps(_holes_for(total)),
                 total, "verified", f"2026-10-{day}T00:00:00")))
        s = self._stats("123")
        self.assertEqual(s["handicap_index"], 0.6)

    def test_handicap_needs_three_rounds(self):
        async def ex(sql, params):
            import aiosqlite
            async with aiosqlite.connect(self.db_path) as con:
                await con.execute(sql, params)
                await con.commit()
        for total in (70, 72):
            run(ex(
                "INSERT INTO scorecards (tournament_id, round_number,"
                " player_discord_id, holes_json, total, status, submitted_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (self.t_open, 1, "123", json.dumps(_holes_for(total)), total,
                 "verified", "2026-10-02T00:00:00")))
        s = self._stats("123")
        self.assertIsNone(s["handicap_index"])

    def test_handicap_uses_last_20_best_8(self):
        # 21 rounds: oldest diff -10 (dropped by the 20-window), then
        # diffs 0..19 -> best 8 = 0..7 -> avg 3.5 * 0.96 = 3.36 -> 3.4.
        async def ex(sql, params):
            import aiosqlite
            async with aiosqlite.connect(self.db_path) as con:
                await con.execute(sql, params)
                await con.commit()
        totals = [62] + [72 + d for d in range(20)]
        for i, total in enumerate(totals):
            run(ex(
                "INSERT INTO scorecards (tournament_id, round_number,"
                " player_discord_id, holes_json, total, status, submitted_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (self.t_open, 1, "123", json.dumps(_holes_for(total)), total,
                 "verified", f"2026-09-{i + 1:02d}T00:00:00")))
        s = self._stats("123")
        self.assertEqual(s["handicap_index"], 3.4)

    # -- privacy ---------------------------------------------------------
    def test_stats_private_blocks_others(self):
        self._card_with_shots(uid="123")
        r = self.patch_profile("123", {"stats_private": True})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["stats_private"])
        r = self.client.get("/api/players/123/stats", headers=self.h("456"))
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["code"], "stats_private")

    def test_stats_private_self_ok(self):
        self.patch_profile("123", {"stats_private": True})
        s = self._stats("123", as_uid="123")
        self.assertEqual(s["player"]["discord_id"], "123")

    def test_stats_public_visible_to_others(self):
        self._card_with_shots(uid="123")
        s = self._stats("123", as_uid="456")
        self.assertEqual(s["rounds_tracked"], 1)

    def test_patch_stats_private_toggle(self):
        r = self.patch_profile("123", {"stats_private": True})
        self.assertTrue(r.json()["stats_private"])
        r = self.patch_profile("123", {"stats_private": False})
        self.assertFalse(r.json()["stats_private"])
        r = self.client.get("/api/players/me", headers=self.h("123"))
        self.assertFalse(r.json()["stats_private"])


# Unbind the imported base class so pytest does not collect ApiTestCase's whole
# suite a second time under this module. Each duplicate suite burns another
# ~50MB of temp DBs in /tmp's 512MB tmpfs and breaks full runs with
# "database or disk is full".
del ApiTestCase

"""Unit tests for tournament-level alt-shot teams (full setup).

- Uses a FRESH temporary SQLite DB (db.init_db) — never the live bot file.
- Monkeypatches main.fetch_* — no network, no Discord calls.
- Teams are tournament-level, persist across rounds, 2 players each.
- Partner picked from registered players (dropdown) or typed as a name.
- Team names are auto-derived and bound to the exact pair: the same two
  players can't form a second team, but either may team with someone else.
- Tee times: one rep per team, max 2 teams per sheet, a player may rep up
  to 3 teams in one round.
- Shared team scorecards via the API; team-level play-in-order.
- Season points: half each, best-position-only for multi-team players.
- unittest only; run with: python -m pytest api/tests/ -q
"""
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR))

from test_api import ApiTestCase, run, GUILD, main  # noqa: E402

from src import db  # noqa: E402

PARS_18 = "4,4,3,4,5,4,3,4,4,4,4,3,4,5,4,3,4,4"


class TournamentTeamsApiTestCase(ApiTestCase):
    """Tournament-level alt-shot teams via the companion API."""

    # -- helpers ------------------------------------------------------
    def _alt(self):
        tid = run(
            db.create_tournament(
                self.db_path, GUILD, "Alt Shot Open", "alt_shot", 18,
                "Pebble Beach Golf Links", PARS_18, "alt", "1",
            )
        )
        return tid

    def _reg(self, uid, handle=None):
        self.with_tz(uid)
        if handle:
            run(db.set_golfplus_handle(self.db_path, uid, handle))
        r = self.client.post(f"/api/tournaments/{self.tid}/register",
                             headers=self.h(uid))
        self.assertEqual(r.status_code, 200, r.text)

    def _team(self, uid, **kw):
        self._ensure_alt()
        return self.client.post(f"/api/tournaments/{self.tid}/teams",
                                headers=self.h(uid), json=kw)

    def _mk_team(self, uid, partner_uid=None, partner_name=None):
        kw = {}
        if partner_uid:
            kw["partner_discord_id"] = partner_uid
        if partner_name:
            kw["partner_name"] = partner_name
        r = self._team(uid, **kw)
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    def _tt(self, uid, team_id, **kw):
        self._ensure_alt()
        body = {"label": "Sheet", "date": "2026-10-03", "time": "09:30",
                "max_players": 4, "team_id": team_id}
        body.update(kw)
        return self.client.post(
            f"/api/tournaments/{self.tid}/tee-times",
            headers=self.h(uid), json=body)

    def setUp(self):
        super().setUp()
        # Lazily created per team test so the inherited ApiTestCase tests
        # (which count tournaments) keep passing unmodified.
        self.tid = None
        for uid, handle in [("1", "Alpha"), ("2", "Bravo"), ("3", "Charlie"),
                            ("4", "Delta"), ("5", "Echo"), ("6", "Foxtrot"),
                            ("7", "Golf")]:
            self.with_tz(uid)
            run(db.set_golfplus_handle(self.db_path, uid, handle))

    def _ensure_alt(self):
        if self.tid is None:
            self.tid = self._alt()
            for uid in ["1", "2", "3", "4", "5", "6", "7"]:
                r = self.client.post(
                    f"/api/tournaments/{self.tid}/register",
                    headers=self.h(uid))
                self.assertEqual(r.status_code, 200, r.text)
        return self.tid

    # -- team creation ------------------------------------------------
    def test_create_team_with_registered_partner(self):
        t = self._mk_team("1", partner_uid="2")
        self.assertEqual(t["name"], "Alpha & Bravo")
        self.assertIsNone(t["partner_name"])
        ids = sorted(m["discord_id"] for m in t["members"])
        self.assertEqual(ids, ["1", "2"])

    def test_create_team_with_typed_name(self):
        t = self._mk_team("1", partner_name="TigerFan99")
        self.assertEqual(t["name"], "Alpha & TigerFan99")
        self.assertEqual(t["partner_name"], "TigerFan99")
        ids = [m["discord_id"] for m in t["members"]]
        self.assertEqual(ids, ["1"])

    def test_create_team_requires_exactly_one_partner(self):
        r = self._team("1")
        self.assertEqual(r.status_code, 422)
        r = self._team("1", partner_discord_id="2", partner_name="X")
        self.assertEqual(r.status_code, 422)

    def test_create_team_self_partner_422(self):
        r = self._team("1", partner_discord_id="1")
        self.assertEqual(r.status_code, 422)

    def test_create_team_partner_must_be_registered(self):
        self.with_tz("8")
        r = self._team("1", partner_discord_id="8")
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["code"], "partner_not_registered")

    def test_create_team_caller_must_be_registered(self):
        self.with_tz("8")
        r = self._team("8", partner_discord_id="2")
        self.assertEqual(r.status_code, 403)

    def test_same_pair_cannot_form_second_team(self):
        self._mk_team("1", partner_uid="2")
        # Same pair, either direction -> 409.
        r = self._team("1", partner_discord_id="2")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["code"], "already_teamed")
        r = self._team("2", partner_discord_id="1")
        self.assertEqual(r.status_code, 409)

    def test_same_pair_typed_name_cannot_reform(self):
        self._mk_team("1", partner_name="TigerFan99")
        r = self._team("1", partner_name="tigerfan99")  # case-insensitive
        self.assertEqual(r.status_code, 409)

    def test_players_can_team_with_others(self):
        self._mk_team("1", partner_uid="2")
        t = self._mk_team("1", partner_uid="3")
        self.assertEqual(t["name"], "Alpha & Charlie")
        t = self._mk_team("2", partner_uid="3")
        self.assertEqual(t["name"], "Bravo & Charlie")

    def test_list_teams(self):
        self._ensure_alt()
        self._mk_team("1", partner_uid="2")
        self._mk_team("3", partner_name="Ghost")
        r = self.client.get(f"/api/tournaments/{self.tid}/teams",
                            headers=self.h("1"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()["teams"]), 2)

    def test_teams_rejected_for_stroke(self):
        self._ensure_alt()
        r = self.client.post(
            f"/api/tournaments/{self.t_open}/teams",
            headers=self.h("1"), json={"partner_discord_id": "2"})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["code"], "not_team_format")

    # -- team deletion -------------------------------------------------
    def test_delete_team_creator(self):
        t = self._mk_team("1", partner_uid="2")
        r = self.client.delete(
            f"/api/tournaments/{self.tid}/teams/{t['id']}",
            headers=self.h("1"))
        self.assertEqual(r.status_code, 200)

    def test_delete_team_non_creator_403(self):
        t = self._mk_team("1", partner_uid="2")
        r = self.client.delete(
            f"/api/tournaments/{self.tid}/teams/{t['id']}",
            headers=self.h("3"))
        self.assertEqual(r.status_code, 403)

    def test_delete_team_blocked_with_submitted_cards(self):
        t = self._mk_team("1", partner_uid="2")
        tt = self._tt("1", t["id"]).json()
        # Start the round and submit the shared team card.
        run(db.start_tournament_tee_time(self.db_path, tt["id"], "1"))
        scores = [4] * 18
        r = self.client.put(
            f"/api/tee-times/{tt['id']}/scorecard", headers=self.h("1"),
            json={"team_id": t["id"], "scores": scores, "round_number": 1,
                  "complete": True})
        self.assertEqual(r.status_code, 200, r.text)
        r = self.client.delete(
            f"/api/tournaments/{self.tid}/teams/{t['id']}",
            headers=self.h("1"))
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["code"], "team_has_cards")

    # -- tee times -----------------------------------------------------
    def test_create_tee_time_requires_team(self):
        r = self._tt("1", None)
        # team_id=None -> omitted; API must 422.
        self.assertEqual(r.status_code, 422)

    def test_create_tee_time_team_capped_at_two(self):
        t1 = self._mk_team("1", partner_uid="2")
        t2 = self._mk_team("3", partner_uid="4")
        t3 = self._mk_team("5", partner_uid="6")
        tt = self._tt("1", t1["id"]).json()
        self.assertEqual(tt["max_players"], 2)
        # Second team joins fine.
        r = self.client.post(f"/api/tee-times/{tt['id']}/join",
                             headers=self.h("3"),
                             json={"team_id": t2["id"]})
        self.assertEqual(r.status_code, 200, r.text)
        # Third team is refused: sheet full.
        r = self.client.post(f"/api/tee-times/{tt['id']}/join",
                             headers=self.h("5"),
                             json={"team_id": t3["id"]})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["code"], "tee_time_full")

    def test_join_requires_being_on_team(self):
        t1 = self._mk_team("1", partner_uid="2")
        tt = self._tt("1", t1["id"]).json()
        r = self.client.post(f"/api/tee-times/{tt['id']}/join",
                             headers=self.h("3"),
                             json={"team_id": t1["id"]})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["code"], "not_on_team")

    def test_team_already_repped(self):
        t1 = self._mk_team("1", partner_uid="2")
        tt = self._tt("1", t1["id"]).json()
        # Cayden 2026-10-12: only the team's creator reps it — the
        # partner can't put the team on a sheet at all.
        r = self.client.post(f"/api/tee-times/{tt['id']}/join",
                             headers=self.h("2"),
                             json={"team_id": t1["id"]})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["code"], "not_team_rep")

    def test_player_three_team_round_cap(self):
        teams = [self._mk_team("1", partner_uid=str(i))["id"]
                 for i in (2, 3, 4)]
        ids = [self._tt("1", tid).json()["id"] for tid in teams]
        self.assertEqual(len(ids), 3)
        # Fourth team in the same round -> 409 round_cap on create.
        t4 = self._mk_team("1", partner_uid="5")
        r = self._tt("1", t4["id"])
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["code"], "round_cap")

    def test_same_team_one_sheet_per_round(self):
        t1 = self._mk_team("1", partner_uid="2")
        tt1 = self._tt("1", t1["id"]).json()
        # The creator books the same team on a different sheet, same
        # round -> blocked: one tee time per team per round.
        r = self.client.post("/api/tournaments/{}/tee-times".format(self.tid),
                             headers=self.h("1"),
                             json={"label": "B", "date": "2026-10-03",
                                   "time": "10:30", "max_players": 4,
                                   "team_id": t1["id"]})
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(r.json()["code"], "team_round_conflict")
        # The partner can't book the team anywhere (not the rep).
        r = self.client.post("/api/tournaments/{}/tee-times".format(self.tid),
                             headers=self.h("2"),
                             json={"label": "C", "date": "2026-10-03",
                                   "time": "11:30", "max_players": 4,
                                   "team_id": t1["id"]})
        self.assertEqual(r.status_code, 403, r.text)
        self.assertEqual(r.json()["code"], "not_team_rep")

    # -- team scorecards ----------------------------------------------
    def _start_and_card(self, tt_id, team_id, uid, scores=None):
        run(db.start_tournament_tee_time(self.db_path, tt_id, uid))
        scores = scores or [4] * 18
        return self.client.put(
            f"/api/tee-times/{tt_id}/scorecard", headers=self.h(uid),
            json={"team_id": team_id, "scores": scores, "round_number": 1,
                  "complete": True})

    def test_shared_team_card(self):
        t1 = self._mk_team("1", partner_uid="2")
        tt = self._tt("1", t1["id"]).json()
        run(db.start_tournament_tee_time(self.db_path, tt["id"], "1"))
        # Rep starts the shared card (partial live save).
        r = self.client.put(
            f"/api/tee-times/{tt['id']}/scorecard", headers=self.h("1"),
            json={"team_id": t1["id"], "scores": [4] * 9 + [None] * 9,
                  "round_number": 1})
        self.assertEqual(r.status_code, 200, r.text)
        # Partner (not on the sheet) can also enter the shared card, and
        # complete it.
        r = self.client.put(
            f"/api/tee-times/{tt['id']}/scorecard", headers=self.h("2"),
            json={"team_id": t1["id"], "scores": [5] * 18,
                  "round_number": 1, "complete": True})
        self.assertEqual(r.status_code, 200, r.text)
        card = r.json()["card"]
        self.assertIsNone(card["player_discord_id"])
        self.assertEqual(card["team_id"], t1["id"])
        # Read back via team_id.
        r = self.client.get(
            f"/api/tee-times/{tt['id']}/scorecard?team_id={t1['id']}",
            headers=self.h("1"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["card"]["total"], 90)

    def test_team_card_non_member_403(self):
        t1 = self._mk_team("1", partner_uid="2")
        tt = self._tt("1", t1["id"]).json()
        run(db.start_tournament_tee_time(self.db_path, tt["id"], "1"))
        r = self.client.put(
            f"/api/tee-times/{tt['id']}/scorecard", headers=self.h("3"),
            json={"team_id": t1["id"], "scores": [4] * 18,
                  "round_number": 1})
        self.assertEqual(r.status_code, 403)

    def test_one_team_card_per_round(self):
        t1 = self._mk_team("1", partner_uid="2")
        tt1 = self._tt("1", t1["id"]).json()
        r = self._start_and_card(tt1["id"], t1["id"], "1")
        self.assertEqual(r.status_code, 200, r.text)
        # Same team, different sheet, same round -> blocked.
        r = self.client.post(
            f"/api/tournaments/{self.tid}/tee-times", headers=self.h("1"),
            json={"label": "B", "date": "2026-10-03", "time": "10:30",
                  "max_players": 4, "team_id": t1["id"]})
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(r.json()["code"], "team_round_conflict")

    # -- season points -------------------------------------------------
    def _finish_two_team_event(self):
        """Two teams, one round; team 1 shoots 72, team 2 shoots 80."""
        t1 = self._mk_team("1", partner_uid="2")
        t2 = self._mk_team("3", partner_name="Ghost")
        tt1 = self._tt("1", t1["id"]).json()
        tt2 = self._tt("3", t2["id"]).json()
        run(db.set_tournament_status(self.db_path, self.tid, "in_progress"))
        r = self._start_and_card(tt1["id"], t1["id"], "1", [4] * 18)
        self.assertEqual(r.status_code, 200, r.text)
        r = self._start_and_card(tt2["id"], t2["id"], "3", [4] * 17 + [12])
        self.assertEqual(r.status_code, 200, r.text)
        # Verify both cards (admin) so final standings count them.
        self._admin(True)
        for tt in (tt1, tt2):
            r = self.client.post(f"/api/admin/tee-times/{tt['id']}/verify",
                                 headers=self.h("1"))
            self.assertEqual(r.status_code, 200, r.text)
        self._admin(False)
        return t1, t2

    def test_half_points_each_and_best_position_only(self):
        from src import leaderboard_render
        t1, t2 = self._finish_two_team_event()
        rows = run(leaderboard_render.final_standings_points(
            self.db_path, self.tid))
        by_pid = {r["player_discord_id"]: r for r in rows}
        # 1st = 100 -> 50 each; 2nd = 90 -> 45 for the registered member.
        # Ghost (typed) earns nothing.
        self.assertEqual(by_pid["1"]["points"], 50)
        self.assertEqual(by_pid["1"]["position"], 1)
        self.assertEqual(by_pid["2"]["points"], 50)
        self.assertEqual(by_pid["3"]["points"], 45)
        self.assertNotIn("Ghost", by_pid)
        self.assertEqual(len(rows), 3)

    def test_multi_team_player_banks_best_only(self):
        from src import leaderboard_render
        # Player 1 on two teams: team A wins, team B finishes 3rd.
        ta = self._mk_team("1", partner_uid="2")
        tb = self._mk_team("1", partner_uid="3")
        tc = self._mk_team("4", partner_uid="5")
        run(db.set_tournament_status(self.db_path, self.tid, "in_progress"))
        tts = {}
        for tid, uid, scores in [
            (ta["id"], "1", [4] * 18),
            (tb["id"], "1", [5] * 18),
            (tc["id"], "4", [4] * 17 + [12]),
        ]:
            tt = self._tt(uid, tid).json()
            tts[tid] = tt
            r = self._start_and_card(tt["id"], tid, uid, scores)
            self.assertEqual(r.status_code, 200, r.text)
        self._admin(True)
        for tt in tts.values():
            self.client.post(f"/api/admin/tee-times/{tt['id']}/verify",
                             headers=self.h("1"))
        self._admin(False)
        rows = run(leaderboard_render.final_standings_points(
            self.db_path, self.tid))
        by_pid = {r["player_discord_id"]: r for r in rows}
        # Player 1: best is 1st -> 50 (not 50 + half of 3rd).
        self.assertEqual(by_pid["1"]["points"], 50)
        self.assertEqual(by_pid["1"]["position"], 1)
        # One row per player even across teams.
        self.assertEqual(
            len([r for r in rows if r["player_discord_id"] == "1"]), 1)

    # -- admin team management (Cayden 2026-10-12) ---------------------
    def _admin_only(self, uid):
        async def fake(discord_id):
            return discord_id == uid
        main.fetch_admin_status = fake

    def test_admin_forms_team_for_two_others(self):
        self._ensure_alt()
        self._admin_only("7")
        r = self.client.post(f"/api/tournaments/{self.tid}/teams",
                             headers=self.h("7"),
                             json={"player_discord_id": "3",
                                   "partner_discord_id": "4"})
        self.assertEqual(r.status_code, 201, r.text)
        team = r.json()
        # Player 1 becomes the creator (and therefore the rep).
        self.assertEqual(team["created_by"], "3")
        self.assertEqual({m["discord_id"] for m in team["members"]},
                         {"3", "4"})
        self.assertEqual(team["name"], "Charlie & Delta")

    def test_non_admin_cannot_form_team_for_others(self):
        self._ensure_alt()
        r = self.client.post(f"/api/tournaments/{self.tid}/teams",
                             headers=self.h("2"),
                             json={"player_discord_id": "3",
                                   "partner_discord_id": "4"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["code"], "admin_only")

    def test_admin_team_player1_must_be_registered(self):
        self._ensure_alt()
        self._admin_only("7")
        r = self.client.post(f"/api/tournaments/{self.tid}/teams",
                             headers=self.h("7"),
                             json={"player_discord_id": "99",
                                   "partner_discord_id": "4"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["code"], "not_registered")

    def test_admin_adds_team_to_tee_time_creator_reps(self):
        t1 = self._mk_team("1", partner_uid="2")
        t2 = self._mk_team("3", partner_uid="4")
        tt = self._tt("1", t1["id"]).json()
        self._admin_only("7")
        r = self.client.post(f"/api/tee-times/{tt['id']}/join",
                             headers=self.h("7"),
                             json={"team_id": t2["id"]})
        self.assertEqual(r.status_code, 200, r.text)
        r = self.client.get(f"/api/tournaments/{self.tid}/tee-times",
                            headers=self.h("7"))
        sheet = next(x for x in r.json() if x["id"] == tt["id"])
        reps = {p["discord_id"]: p.get("team_id")
                for p in sheet["players"]}
        self.assertEqual(reps.get("3"), t2["id"])  # creator reps
        self.assertNotIn("7", reps)                # admin not on sheet

    def test_admin_add_team_already_on_other_sheet_409(self):
        t1 = self._mk_team("1", partner_uid="2")
        t2 = self._mk_team("3", partner_uid="4")
        tt1 = self._tt("1", t1["id"]).json()
        self._tt("3", t2["id"])
        self._admin_only("7")
        r = self.client.post(f"/api/tee-times/{tt1['id']}/join",
                             headers=self.h("7"),
                             json={"team_id": t2["id"]})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["code"], "team_round_conflict")

    def test_admin_creates_tee_time_for_team(self):
        t2 = self._mk_team("3", partner_uid="4")
        self._admin_only("7")
        r = self.client.post(f"/api/tournaments/{self.tid}/tee-times",
                             headers=self.h("7"),
                             json={"label": "Admin sheet",
                                   "date": "2026-10-03", "time": "08:00",
                                   "max_players": 4,
                                   "team_id": t2["id"]})
        self.assertEqual(r.status_code, 200, r.text)
        players = r.json()["players"]
        self.assertEqual([p["discord_id"] for p in players], ["3"])

    def test_request_requires_creator(self):
        t1 = self._mk_team("1", partner_uid="2")
        t2 = self._mk_team("3", partner_uid="4")
        tt = self._tt("3", t2["id"]).json()
        r = self.client.post(f"/api/tee-times/{tt['id']}/request",
                             headers=self.h("2"),
                             json={"team_id": t1["id"]})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["code"], "not_team_rep")

    def test_either_team_can_enter_both_cards(self):
        t1 = self._mk_team("1", partner_uid="2")
        t2 = self._mk_team("3", partner_uid="4")
        tt = self._tt("1", t1["id"]).json()
        r = self.client.post(f"/api/tee-times/{tt['id']}/join",
                             headers=self.h("3"),
                             json={"team_id": t2["id"]})
        self.assertEqual(r.status_code, 200, r.text)
        run(db.start_tournament_tee_time(self.db_path, tt["id"], "1"))
        # Partner of team 1 (not on the sheet) enters team 2's card.
        r = self.client.put(f"/api/tee-times/{tt['id']}/scorecard",
                            headers=self.h("2"),
                            json={"team_id": t2["id"], "scores": [4] * 18,
                                  "round_number": 1})
        self.assertEqual(r.status_code, 200, r.text)
        # Partner of team 2 can read team 1's card.
        r = self.client.get(
            f"/api/tee-times/{tt['id']}/scorecard?team_id={t1['id']}",
            headers=self.h("4"))
        self.assertEqual(r.status_code, 200, r.text)
        # A registered player with no team on this sheet cannot enter.
        r = self.client.put(f"/api/tee-times/{tt['id']}/scorecard",
                            headers=self.h("5"),
                            json={"team_id": t1["id"], "scores": [4] * 18,
                                  "round_number": 1})
        self.assertEqual(r.status_code, 403)

    def test_best_ball_cards_are_per_player(self):
        tid = run(
            db.create_tournament(
                self.db_path, GUILD, "Best Ball Open", "best_ball", 18,
                "Pebble Beach Golf Links", PARS_18, "bb", "1",
            )
        )
        for uid in ("1", "2"):
            r = self.client.post(f"/api/tournaments/{tid}/register",
                                 headers=self.h(uid))
            self.assertEqual(r.status_code, 200, r.text)
        r = self.client.post(f"/api/tournaments/{tid}/teams",
                             headers=self.h("1"),
                             json={"partner_discord_id": "2"})
        self.assertEqual(r.status_code, 201, r.text)
        team = r.json()
        r = self.client.post(f"/api/tournaments/{tid}/tee-times",
                             headers=self.h("1"),
                             json={"label": "BB", "date": "2026-10-03",
                                   "time": "09:00", "max_players": 4,
                                   "team_id": team["id"]})
        self.assertEqual(r.status_code, 200, r.text)
        tt = r.json()
        run(db.start_tournament_tee_time(self.db_path, tt["id"], "1"))
        # Partner files their own best-ball card (player + team).
        r = self.client.put(f"/api/tee-times/{tt['id']}/scorecard",
                            headers=self.h("2"),
                            json={"player_discord_id": "2",
                                  "team_id": team["id"],
                                  "scores": [3] * 18, "round_number": 1})
        self.assertEqual(r.status_code, 200, r.text)
        # The rep files a different card.
        r = self.client.put(f"/api/tee-times/{tt['id']}/scorecard",
                            headers=self.h("1"),
                            json={"player_discord_id": "1",
                                  "team_id": team["id"],
                                  "scores": [5] * 18, "round_number": 1})
        self.assertEqual(r.status_code, 200, r.text)
        # GET with team + player returns THAT player's card.
        r = self.client.get(
            f"/api/tee-times/{tt['id']}/scorecard"
            f"?team_id={team['id']}&player_discord_id=2",
            headers=self.h("1"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["card"]["total"], 54)
        r = self.client.get(
            f"/api/tee-times/{tt['id']}/scorecard"
            f"?team_id={team['id']}&player_discord_id=1",
            headers=self.h("1"))
        self.assertEqual(r.json()["card"]["total"], 90)


del ApiTestCase

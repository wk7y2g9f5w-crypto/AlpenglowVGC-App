"""Unit tests for team-name removal (AltShot + Matchplay).

- Uses a FRESH temporary SQLite DB (db.init_db) — never the live bot file.
- Monkeypatches main.fetch_discord_user / fetch_crew_status /
  fetch_mod_admin_status: no network, no Discord calls.
- Rule under test: team names no longer exist anywhere. AltShot and
  Matchplay sides are identified purely by their registered players;
  any team-name fields sent on create/update/join are ignored (never
  422) and create no team_name_registry rows. The registry table and
  helpers remain in the schema but are unused.
- unittest only; run with: python -m pytest tests/ -q
"""
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR))

from test_api import ApiTestCase, run  # noqa: E402

from src import db  # noqa: E402

COURSE = "Pebble Beach Golf Links"
STARTS_AT = "2030-10-03T14:00:00Z"


class TeamNameLockApiTestCase(ApiTestCase):
    """Team names are gone from AltShot and Matchplay."""

    # -- helpers ------------------------------------------------------
    def _mp_create(self, uid="1", **over):
        body = {
            "label": "Sat match",
            "course": COURSE,
            "starts_at": STARTS_AT,
            "format": "bestball",
            "team_size": 2,
        }
        body.update(over)
        return self.client.post("/api/matchplay/tee-times",
                                headers=self.h(uid), json=body)

    def _mp_join(self, tt_id, uid, side):
        return self.client.post(
            f"/api/matchplay/tee-times/{tt_id}/join", headers=self.h(uid),
            json={"side_number": side})

    def _as_create(self, uid="1", **over):
        body = {
            "label": "Sat alt-shot",
            "course": COURSE,
            "starts_at": STARTS_AT,
            "max_teams": 2,
        }
        body.update(over)
        return self.client.post("/api/altshot-tee-times",
                                headers=self.h(uid), json=body)

    def _as_join(self, tt_id, uid, **over):
        body = {}
        body.update(over)
        return self.client.post(
            f"/api/altshot-tee-times/{tt_id}/join", headers=self.h(uid),
            json=body)

    def _as_team_url(self, tt_id, team_id):
        return f"/api/altshot-tee-times/{tt_id}/teams/{team_id}"

    def _as_rename(self, tt_id, team_id, uid, name):
        return self.client.patch(
            self._as_team_url(tt_id, team_id), headers=self.h(uid),
            json={"team_name": name})

    def _as_submit(self, tt_id, team_id, uid):
        return self.client.post(
            self._as_team_url(tt_id, team_id) + "/score",
            headers=self.h(uid), json={"holes": [4] * 18})

    # -- normalize (registry helpers still exist, unused) --------------
    def test_normalize_team_name(self):
        self.assertEqual(db.normalize_team_name("  Eagles\tFC "), "eagles fc")
        self.assertEqual(db.normalize_team_name("EAGLES"), "eagles")
        self.assertEqual(db.normalize_team_name("   "), "")
        self.assertEqual(db.normalize_team_name(None), "")

    # -- matchplay: no team names at all -----------------------------
    def test_matchplay_ignores_team_names_no_claim(self):
        # Matchplay sides are identified by their players; any team-name
        # fields sent are ignored and create no registry claim.
        r = self._mp_create("1", side1_team_name="Eagles",
                            side2_team_name="Kings")
        self.assertEqual(r.status_code, 200, r.text)
        tt = r.json()
        self.assertNotIn("team_name", tt["sides"][0])
        self.assertNotIn("team_name", tt["sides"][1])
        self.assertEqual(tt["sides"][0]["display_name"], "User1")
        self.assertIsNone(run(db._team_name_row(self.db_path, "eagles")))
        self.assertIsNone(run(db._team_name_row(self.db_path, "kings")))

    def test_matchplay_join_never_claims(self):
        tt = self._mp_create("1").json()
        r = self._mp_join(tt["id"], "2", 1)
        self.assertEqual(r.status_code, 200, r.text)
        tt = self.client.get(f"/api/matchplay/tee-times/{tt['id']}",
                             headers=self.h("1")).json()
        for s in tt["sides"]:
            self.assertNotIn("team_name", s)

    def test_matchplay_update_ignores_team_names(self):
        tt = self._mp_create("1").json()
        r = self.client.patch(f"/api/matchplay/tee-times/{tt['id']}",
                              headers=self.h("1"),
                              json={"side1_team_name": "Eagles"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertNotIn("team_name", r.json()["sides"][0])
        self.assertIsNone(run(db._team_name_row(self.db_path, "eagles")))

    # -- altshot: no team names at all --------------------------------
    def test_altshot_create_ignores_team_names_no_claim(self):
        # Team names are gone from AltShot: create fields are ignored
        # (not 422), no registry rows are created, teams are "Team N"
        # until players join.
        r = self._as_create("1", team1_name="Eagles", team2_name="Falcons")
        self.assertEqual(r.status_code, 200, r.text)
        tt = r.json()
        teams = {t["team_number"]: t for t in tt["teams"]}
        self.assertNotIn("team_name", teams[1])
        self.assertNotIn("team_name", teams[2])
        self.assertEqual(teams[1]["display_name"], "Team 1")
        self.assertEqual(teams[2]["display_name"], "Team 2")
        self.assertIsNone(run(db._team_name_row(self.db_path, "eagles")))
        self.assertIsNone(run(db._team_name_row(self.db_path, "falcons")))
        # A second crew may use the same "name" freely.
        r = self._as_create("3", team1_name="Eagles", team2_name="Falcons")
        self.assertEqual(r.status_code, 200, r.text)

    def test_altshot_join_never_claims(self):
        tt = self._as_create("1", max_teams=1, team_size=3).json()
        team_id = tt["teams"][0]["id"]
        for uid in ("2", "3"):
            r = self._as_join(tt["id"], uid, team_name="Eagles")
            self.assertEqual(r.status_code, 200, r.text)
        tt = self.client.get(f"/api/altshot-tee-times/{tt['id']}",
                             headers=self.h("1")).json()
        self.assertNotIn("team_name", tt["teams"][0])
        self.assertEqual(tt["teams"][0]["display_name"],
                         "User1 & User2 & User3")
        self.assertIsNone(run(db._team_name_row(self.db_path, "eagles")))

    def test_altshot_update_ignores_team_names(self):
        tt = self._as_create("1", max_teams=1, team_size=2).json()
        team_id = tt["teams"][0]["id"]
        r = self._as_rename(tt["id"], team_id, "1", "Eagles")
        self.assertEqual(r.status_code, 200, r.text)
        teams = {t["id"]: t for t in
                 self.client.get(f"/api/altshot-tee-times/{tt['id']}",
                                 headers=self.h("1")).json()["teams"]}
        self.assertNotIn("team_name", teams[team_id])
        self.assertEqual(teams[team_id]["display_name"], "User1")
        self.assertIsNone(run(db._team_name_row(self.db_path, "eagles")))

    def test_altshot_manage_ignores_team_names(self):
        # Fixed 2-team manage: renames are gone; move/remove still work.
        tt = self._as_create("1").json()
        teams = {t["team_number"]: t for t in tt["teams"]}
        r = self.client.post(
            f"/api/altshot-tee-times/{tt['id']}/join", headers=self.h("2"),
            json={"team_id": teams[2]["id"]})
        self.assertEqual(r.status_code, 200, r.text)
        # team_name in the manage payload is ignored, move still works.
        r = self.client.patch(
            self._as_team_url(tt["id"], teams[1]["id"]), headers=self.h("1"),
            json={"team_name": "Eagles", "move_discord_id": "2"})
        self.assertEqual(r.status_code, 200, r.text)
        teams = {t["team_number"]: t for t in r.json()["teams"]}
        self.assertNotIn("team_name", teams[1])
        self.assertEqual(teams[1]["member_discord_ids"], ["1", "2"])
        self.assertIsNone(run(db._team_name_row(self.db_path, "eagles")))

    def test_altshot_score_submit_never_claims(self):
        # Scoring creates no registry claims.
        tt = self._as_create("1", max_teams=1, team_size=2).json()
        team_id = tt["teams"][0]["id"]
        self._as_join(tt["id"], "2")
        r = self._as_submit(tt["id"], team_id, "1")
        self.assertEqual(r.status_code, 200, r.text)
        rows = run(db._fetchall(
            self.db_path, "SELECT * FROM team_name_registry", ()))
        self.assertEqual(rows, [])

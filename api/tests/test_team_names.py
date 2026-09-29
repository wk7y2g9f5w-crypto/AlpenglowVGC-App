"""Unit tests for the global team-name lock.

- Uses a FRESH temporary SQLite DB (db.init_db) — never the live bot file.
- Monkeypatches main.fetch_discord_user / fetch_crew_status /
  fetch_mod_admin_status: no network, no Discord calls.
- Rule under test: once a team name is chosen it is locked to the crew
  of players who first used it — no other group of players may play
  under that name. Players stay free to play under *different* names
  with other people. The registry covers AltShot only; matchplay has no
  team names and never touches it.
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
    """Team-name locking (AltShot). Matchplay has no team names."""

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

    # -- normalize ----------------------------------------------------
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

    # -- altshot -------------------------------------------------------
    def test_altshot_fixed_roster_name_lock(self):
        tt = self._as_create("1", max_teams=1, team_size=2).json()
        team_id = tt["teams"][0]["id"]
        r = self._as_rename(tt["id"], team_id, "1", "Eagles")
        self.assertEqual(r.status_code, 200, r.text)
        # Another crew's team may not take the name (case-insensitive).
        tt2 = self._as_create("3", max_teams=1, team_size=2).json()
        r = self._as_rename(tt2["id"], tt2["teams"][0]["id"], "3", "eagles")
        self.assertEqual(r.status_code, 409, r.text)
        # Fill the roster -> finalized to {1, 2}; the creator alone may
        # reuse it later.
        r = self._as_join(tt["id"], "2")
        self.assertEqual(r.status_code, 200, r.text)
        tt3 = self._as_create("1", max_teams=1, team_size=2).json()
        r = self._as_rename(tt3["id"], tt3["teams"][0]["id"], "1", "Eagles")
        self.assertEqual(r.status_code, 200, r.text)

    def test_altshot_fixed_roster_outsider_join_409(self):
        tt = self._as_create("1", max_teams=1, team_size=3).json()
        team_id = tt["teams"][0]["id"]
        self._as_rename(tt["id"], team_id, "1", "Eagles")
        r = self._as_join(tt["id"], "2")  # pending claim -> anyone may join
        self.assertEqual(r.status_code, 200, r.text)
        r = self._as_join(tt["id"], "3")  # roster full -> finalized {1,2,3}
        self.assertEqual(r.status_code, 200, r.text)
        # New tee time, same crew name claimed by user 1; user 4 is an
        # outsider to the owning crew.
        tt2 = self._as_create("1", max_teams=1, team_size=3).json()
        team2 = tt2["teams"][0]["id"]
        r = self._as_rename(tt2["id"], team2, "1", "Eagles")
        self.assertEqual(r.status_code, 200, r.text)
        r = self._as_join(tt2["id"], "4")
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn("belongs to another crew", r.json()["detail"])

    def test_altshot_two_team_create_name_lock(self):
        # Team names come from creation on fixed 2-team tee times; a
        # second crew may not claim the same name, and a failed create
        # leaves nothing behind.
        r = self._as_create("1", team2_name="Eagles")
        self.assertEqual(r.status_code, 200, r.text)
        r = self._as_create("3", team2_name="eagles")
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn("taken by another crew", r.json()["detail"])
        r = self.client.get("/api/altshot-tee-times", headers=self.h("3"))
        self.assertEqual(len(r.json()["tee_times"]), 1)

    def test_altshot_two_team_finalize_when_full(self):
        tt = self._as_create("1", team1_name="Eagles").json()
        teams = {t["team_number"]: t for t in tt["teams"]}
        # Fill team 1 -> the name finalizes to {1, 2}.
        for uid in ("2",):
            r = self._as_join(tt["id"], uid, team_id=teams[1]["id"])
            self.assertEqual(r.status_code, 200, r.text)
        # "Eagles" now belongs to {1, 2}: user 3 may not take it...
        tt2 = self._as_create("3").json()
        team2 = tt2["teams"][0]
        r = self._as_rename(tt2["id"], team2["id"], "3", "Eagles")
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn("belongs to another crew", r.json()["detail"])
        # ...but an original crew member may reuse it as a subset.
        tt3 = self._as_create("2").json()
        r = self._as_rename(tt3["id"], tt3["teams"][0]["id"], "2", "Eagles")
        self.assertEqual(r.status_code, 200, r.text)

    # -- matchplay names never enter the registry ----------------------
    def test_matchplay_name_does_not_block_altshot(self):
        # A name sent on a matchplay create is ignored, so an AltShot team
        # may freely claim it afterwards.
        self._mp_create("1", side1_team_name="Eagles")
        tt = self._as_create("3", max_teams=1, team_size=2).json()
        r = self._as_rename(tt["id"], tt["teams"][0]["id"], "3", "Eagles")
        self.assertEqual(r.status_code, 200, r.text)

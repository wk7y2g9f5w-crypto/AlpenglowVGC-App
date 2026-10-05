"""API tests for the upcoming-list window.

list_casual_tee_times, list_altshot_tee_times, and list_matchplay_tee_times
treat a start up to 3 hours in the past as still upcoming. A four-man round
takes ~2h and the list entry is the only path to score entry, so tee times
must not drop off mid-round. (Backdating a tee time up to 10 minutes at
setup still works — creation never rejected past starts.)

Uses the ApiTestCase harness from test_api.py (fresh temp DB per test).
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_api import ApiTestCase  # noqa: E402

COURSE = "Pebble Beach Golf Links"


def _starts_at(minutes_from_now):
    return (datetime.now(timezone.utc)
            + timedelta(minutes=minutes_from_now)).isoformat()


class UpcomingGraceTest(ApiTestCase):
    # -- helpers ------------------------------------------------------
    def _mk_casual(self, uid, starts_at):
        r = self.client.post(
            "/api/casual-tee-times", headers=self.h(uid),
            json={"label": "Grace round", "course": COURSE,
                  "starts_at": starts_at, "format": "stroke"})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["id"]

    def _mk_matchplay(self, uid, starts_at):
        r = self.client.post(
            "/api/matchplay/tee-times", headers=self.h(uid),
            json={"label": "Grace MP", "course": COURSE,
                  "starts_at": starts_at, "format": "single"})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["id"]

    def _mk_altshot(self, uid, starts_at):
        r = self.client.post(
            "/api/altshot-tee-times", headers=self.h(uid),
            json={"label": "Grace AS", "course": COURSE,
                  "starts_at": starts_at, "max_teams": 1, "team_size": 2})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["id"]

    def _casual_ids(self, uid):
        r = self.client.get("/api/casual-tee-times", headers=self.h(uid))
        self.assertEqual(r.status_code, 200, r.text)
        return [t["id"] for t in r.json()["tee_times"]]

    def _matchplay_ids(self, uid):
        r = self.client.get("/api/matchplay/tee-times", headers=self.h(uid))
        self.assertEqual(r.status_code, 200, r.text)
        return [t["id"] for t in r.json()["tee_times"]]

    def _altshot_ids(self, uid):
        r = self.client.get("/api/altshot-tee-times", headers=self.h(uid))
        self.assertEqual(r.status_code, 200, r.text)
        return [t["id"] for t in r.json()["tee_times"]]

    # -- casual -------------------------------------------------------
    def test_casual_2h_past_is_listed(self):
        tt_id = self._mk_casual("123", _starts_at(-120))
        self.assertIn(tt_id, self._casual_ids("123"))

    def test_casual_3h1m_past_is_not_listed(self):
        tt_id = self._mk_casual("123", _starts_at(-181))
        self.assertNotIn(tt_id, self._casual_ids("123"))

    def test_casual_future_still_listed(self):
        tt_id = self._mk_casual("123", _starts_at(60))
        self.assertIn(tt_id, self._casual_ids("123"))

    # -- matchplay ----------------------------------------------------
    def test_matchplay_2h_past_is_listed(self):
        tt_id = self._mk_matchplay("123", _starts_at(-120))
        self.assertIn(tt_id, self._matchplay_ids("123"))

    def test_matchplay_3h1m_past_is_not_listed(self):
        tt_id = self._mk_matchplay("123", _starts_at(-181))
        self.assertNotIn(tt_id, self._matchplay_ids("123"))

    # -- altshot ------------------------------------------------------
    def test_altshot_2h_past_is_listed(self):
        tt_id = self._mk_altshot("123", _starts_at(-120))
        self.assertIn(tt_id, self._altshot_ids("123"))

    def test_altshot_3h1m_past_is_not_listed(self):
        tt_id = self._mk_altshot("123", _starts_at(-181))
        self.assertNotIn(tt_id, self._altshot_ids("123"))


# Unbind the imported base class so pytest does not collect ApiTestCase's whole
# suite a second time under this module. Each duplicate suite burns another
# ~50MB of temp DBs in /tmp's 512MB tmpfs and breaks full runs with
# "database or disk is full".
del ApiTestCase

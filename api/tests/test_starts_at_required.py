"""Start date/time is mandatory for AltShot and Matchplay tee times.

Cayden: "This is the only verification tool we have to validate round
legitimacy." Casual tee times are exempt (non-leaderboard rounds), and
tournament tee times already require date/time via parse_in_tz.

- POST without starts_at / with blank starts_at -> 422
- PATCH that clears starts_at -> 422
- valid starts_at -> 200
"""
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR))

from test_api import ApiTestCase  # noqa: E402

COURSE = "Pebble Beach Golf Links"
WHEN = "2030-10-03T14:00:00Z"


class StartsAtRequiredApiTestCase(ApiTestCase):
    # -- altshot ------------------------------------------------------
    def _as_body(self, **over):
        body = {"label": "Sat alt-shot", "course": COURSE,
                "starts_at": WHEN, "max_teams": 2}
        body.update(over)
        return body

    def _as_post(self, uid="1", **over):
        return self.client.post("/api/altshot-tee-times",
                                headers=self.h(uid),
                                json=self._as_body(**over))

    def test_altshot_create_requires_starts_at(self):
        for bad in (None, "", "   "):
            body = self._as_body()
            if bad is None:
                del body["starts_at"]
            else:
                body["starts_at"] = bad
            r = self.client.post("/api/altshot-tee-times",
                                 headers=self.h("1"), json=body)
            self.assertEqual(r.status_code, 422, (bad, r.text))

    def test_altshot_create_ok_with_starts_at(self):
        r = self._as_post()
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["starts_at"], WHEN)

    def test_altshot_patch_cannot_clear_starts_at(self):
        tt = self._as_post().json()
        r = self.client.patch(f"/api/altshot-tee-times/{tt['id']}",
                              headers=self.h("1"),
                              json={"starts_at": ""})
        self.assertEqual(r.status_code, 422, r.text)
        r = self.client.patch(f"/api/altshot-tee-times/{tt['id']}",
                              headers=self.h("1"),
                              json={"starts_at": "2030-11-04T15:00:00Z"})
        self.assertEqual(r.status_code, 200, r.text)

    # -- matchplay -----------------------------------------------------
    def _mp_body(self, **over):
        body = {"label": "Sat match", "course": COURSE,
                "starts_at": WHEN, "format": "single"}
        body.update(over)
        return body

    def _mp_post(self, uid="1", **over):
        return self.client.post("/api/matchplay/tee-times",
                                headers=self.h(uid),
                                json=self._mp_body(**over))

    def test_matchplay_create_requires_starts_at(self):
        for bad in (None, "", "   "):
            body = self._mp_body()
            if bad is None:
                del body["starts_at"]
            else:
                body["starts_at"] = bad
            r = self.client.post("/api/matchplay/tee-times",
                                 headers=self.h("1"), json=body)
            self.assertEqual(r.status_code, 422, (bad, r.text))

    def test_matchplay_create_ok_with_starts_at(self):
        r = self._mp_post()
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["starts_at"], WHEN)

    def test_matchplay_patch_cannot_clear_starts_at(self):
        tt = self._mp_post().json()
        r = self.client.patch(f"/api/matchplay/tee-times/{tt['id']}",
                              headers=self.h("1"),
                              json={"starts_at": "  "})
        self.assertEqual(r.status_code, 422, r.text)
        r = self.client.patch(f"/api/matchplay/tee-times/{tt['id']}",
                              headers=self.h("1"),
                              json={"label": "Renamed"})
        self.assertEqual(r.status_code, 200, r.text)

    # -- tournament tee times (already required) -----------------------
    def test_tournament_tee_time_requires_date_time(self):
        # date/time are required model fields; empty values fail parsing.
        self.with_tz("1")
        r = self.client.post(
            f"/api/tournaments/{self.t_open}/tee-times",
            headers=self.h("1"),
            json={"label": "x", "date": "", "time": ""})
        self.assertEqual(r.status_code, 422, r.text)

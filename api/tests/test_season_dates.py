"""Tests for PATCH /api/seasons/{id} — adjusting the active season's dates."""

from test_api import ApiTestCase as _ApiTestCase, run, main, GUILD
from src import db


class SeasonDateApiTestCase(_ApiTestCase):
    def _season(self, **kw):
        kw.setdefault("start_date", "2026-10-01")
        kw.setdefault("end_date", "2027-03-31")
        self._admin(True)
        r = self.client.post("/api/seasons", headers=self.h("1"),
                             json={"name": "Winter 2026", **kw})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["id"]

    def test_update_dates_happy(self):
        sid = self._season()
        r = self.client.patch(f"/api/seasons/{sid}", headers=self.h("1"),
                              json={"start_date": "2026-11-01",
                                    "end_date": "2027-04-30"})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["start_date"], "2026-11-01")
        self.assertEqual(body["end_date"], "2027-04-30")
        self.assertEqual(body["status"], "active")
        season = run(db.get_season(self.db_path, sid))
        self.assertEqual(season["start_date"], "2026-11-01")
        self.assertEqual(season["end_date"], "2027-04-30")

    def test_update_single_date_keeps_other(self):
        sid = self._season()
        r = self.client.patch(f"/api/seasons/{sid}", headers=self.h("1"),
                              json={"end_date": "2027-06-30"})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["end_date"], "2027-06-30")
        # Omitted start_date is left alone, not cleared.
        self.assertEqual(body["start_date"], "2026-10-01")

    def test_single_date_validated_against_stored(self):
        sid = self._season()
        # New end before the stored start must fail.
        r = self.client.patch(f"/api/seasons/{sid}", headers=self.h("1"),
                              json={"end_date": "2026-09-01"})
        self.assertEqual(r.status_code, 422, r.text)
        self.assertEqual(r.json()["code"], "invalid_dates")

    def test_clear_dates(self):
        sid = self._season()
        r = self.client.patch(f"/api/seasons/{sid}", headers=self.h("1"),
                              json={"start_date": None, "end_date": None})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIsNone(r.json()["start_date"])
        self.assertIsNone(r.json()["end_date"])

    def test_start_after_end_422(self):
        sid = self._season()
        r = self.client.patch(f"/api/seasons/{sid}", headers=self.h("1"),
                              json={"start_date": "2027-05-01",
                                    "end_date": "2027-04-01"})
        self.assertEqual(r.status_code, 422, r.text)

    def test_bad_date_format_422(self):
        sid = self._season()
        r = self.client.patch(f"/api/seasons/{sid}", headers=self.h("1"),
                              json={"start_date": "not-a-date"})
        self.assertEqual(r.status_code, 422, r.text)

    def test_missing_season_404(self):
        self._admin(True)
        r = self.client.patch("/api/seasons/9999", headers=self.h("1"),
                              json={"end_date": "2027-04-30"})
        self.assertEqual(r.status_code, 404, r.text)
        self.assertEqual(r.json()["code"], "season_not_found")

    def test_completed_season_409(self):
        sid = self._season()
        r = self.client.post(f"/api/seasons/{sid}/complete",
                             headers=self.h("1"))
        self.assertEqual(r.status_code, 200, r.text)
        r = self.client.patch(f"/api/seasons/{sid}", headers=self.h("1"),
                              json={"end_date": "2027-04-30"})
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(r.json()["code"], "season_not_active")

    def test_non_admin_403(self):
        self._admin(False)
        sid = run(db.create_season(self.db_path, GUILD, "Fall 2026", "1",
                                   start_date="2026-10-01",
                                   end_date="2027-03-31"))
        r = self.client.patch(f"/api/seasons/{sid}", headers=self.h("1"),
                              json={"end_date": "2027-04-30"})
        self.assertEqual(r.status_code, 403, r.text)


# Prevent pytest from re-collecting the whole inherited ApiTestCase suite
# under this module (see AGENTS.md).
del _ApiTestCase

"""Tests for the Start Round / Archive flow."""

from test_api import ApiTestCase as _ApiTestCase, run
from src import db


class StartRoundApiTestCase(_ApiTestCase):
    def _started_tt(self, tournament, creator="123", extra=()):
        tt_id = run(db.create_tee_time(
            self.db_path, tournament, "flight",
            "2026-01-02T15:30:00+00:00", 4, creator, None))
        run(db.join_tee_time(self.db_path, tt_id, creator))
        for pid in extra:
            run(db.join_tee_time(self.db_path, tt_id, pid))
        return tt_id

    def test_start_round_happy(self):
        tt_id = self._started_tt(self.t_open, creator="123")
        r = self.client.post(f"/api/tee-times/{tt_id}/start",
                             headers=self.h("123"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["started"])
        self.assertIsNotNone(r.json()["started_at"])

    def test_start_round_not_player_403(self):
        tt_id = self._started_tt(self.t_open, creator="123")
        r = self.client.post(f"/api/tee-times/{tt_id}/start",
                             headers=self.h("456"))
        self.assertEqual(r.status_code, 403, r.text)

    def test_start_round_twice_409(self):
        tt_id = self._started_tt(self.t_open, creator="123")
        r = self.client.post(f"/api/tee-times/{tt_id}/start",
                             headers=self.h("123"))
        self.assertEqual(r.status_code, 200, r.text)
        r = self.client.post(f"/api/tee-times/{tt_id}/start",
                             headers=self.h("123"))
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(r.json()["code"], "already_started")

    def test_join_locked_after_start(self):
        tt_id = self._started_tt(self.t_open, creator="123")
        r = self.client.post(f"/api/tee-times/{tt_id}/start",
                             headers=self.h("123"))
        self.assertEqual(r.status_code, 200, r.text)
        r = self.client.post(f"/api/tee-times/{tt_id}/join",
                             headers=self.h("456"))
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(r.json()["code"], "round_started")

    def test_scorecard_gated_on_start(self):
        tt_id = self._started_tt(self.t_open, creator="123")
        body = {"player_discord_id": "123", "scores": [4] * 18,
                "round_number": 1}
        # Before Start Round: 409.
        r = self.client.put(f"/api/tee-times/{tt_id}/scorecard",
                            headers=self.h("123"), json=body)
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(r.json()["code"], "round_not_started")
        # After Start Round: 200.
        r = self.client.post(f"/api/tee-times/{tt_id}/start",
                             headers=self.h("123"))
        self.assertEqual(r.status_code, 200, r.text)
        r = self.client.put(f"/api/tee-times/{tt_id}/scorecard",
                            headers=self.h("123"), json=body)
        self.assertEqual(r.status_code, 200, r.text)

    def test_archive_on_all_submitted(self):
        tt_id = self._started_tt(self.t_open, creator="123",
                                 extra=("456",))
        run(db.register_player(self.db_path, self.t_open, "123"))
        run(db.register_player(self.db_path, self.t_open, "456"))
        self.client.post(f"/api/tee-times/{tt_id}/start",
                         headers=self.h("123"))
        body = {"player_discord_id": "123", "scores": [4] * 18,
                "round_number": 1, "complete": True}
        r = self.client.put(f"/api/tee-times/{tt_id}/scorecard",
                            headers=self.h("123"), json=body)
        self.assertEqual(r.status_code, 200, r.text)
        # One of two submitted: not archived yet.
        tt = run(db.get_tee_time(self.db_path, tt_id))
        self.assertIsNone(tt.get("archived_at"))
        body["player_discord_id"] = "456"
        r = self.client.put(f"/api/tee-times/{tt_id}/scorecard",
                            headers=self.h("456"), json=body)
        self.assertEqual(r.status_code, 200, r.text)
        # Both submitted: archived.
        tt = run(db.get_tee_time(self.db_path, tt_id))
        self.assertIsNotNone(tt.get("archived_at"))
        # Archived tee time no longer in the active list.
        r = self.client.get(
            f"/api/tournaments/{self.t_open}/tee-times",
            headers=self.h("123"))
        ids = [t["id"] for t in r.json()]
        self.assertNotIn(tt_id, ids)

    def test_archive_endpoints_admin_only(self):
        # Non-admin: 403.
        r = self.client.get("/api/admin/archive/tournament-tee-times",
                            headers=self.h("123"))
        self.assertEqual(r.status_code, 403, r.text)
        # Admin: 200 with solo/group keys.
        self._admin(True)
        r = self.client.get("/api/admin/archive/tournament-tee-times",
                            headers=self.h("123"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("solo", r.json())
        self.assertIn("group", r.json())

    def test_archived_shows_in_admin_archive(self):
        tt_id = self._started_tt(self.t_open, creator="123")
        run(db.register_player(self.db_path, self.t_open, "123"))
        self.client.post(f"/api/tee-times/{tt_id}/start",
                         headers=self.h("123"))
        body = {"player_discord_id": "123", "scores": [4] * 18,
                "round_number": 1, "complete": True}
        # Solo card stays pending; force-verify via db for the archive check.
        r = self.client.put(f"/api/tee-times/{tt_id}/scorecard",
                            headers=self.h("123"), json=body)
        self.assertEqual(r.status_code, 200, r.text)
        card = run(db.find_scorecard(
            self.db_path, self.t_open, player_discord_id="123",
            tee_time_id=tt_id, round_number=1))
        run(db._execute(
            self.db_path,
            "UPDATE scorecards SET status = 'verified' WHERE id = ?",
            (card["id"],)))
        self.assertTrue(run(
            db.archive_tournament_tee_time_if_complete(
                self.db_path, tt_id)))
        self._admin(True)
        r = self.client.get("/api/admin/archive/tournament-tee-times",
                            headers=self.h("123"))
        self.assertEqual(r.status_code, 200, r.text)
        # Solo player -> solo bucket, with started_at for the audit trail.
        self.assertEqual(len(r.json()["solo"]), 1)
        entry = r.json()["solo"][0]
        self.assertIsNotNone(entry["started_at"])
        self.assertEqual(len(r.json()["group"]), 0)
del _ApiTestCase

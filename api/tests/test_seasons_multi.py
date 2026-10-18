"""Side-by-side seasons (Cayden 2026-10-18).

Multiple seasons may be active at once. Creating a tournament picks
which season its standings go to (no pick = counts for none, with a
warning shown in the app/Discord before continuing). The assignment
can be corrected later via PUT /api/tournaments/{id}/season — points
move with it, including for completed tournaments.
"""
from test_api import ApiTestCase, run, GUILD, main  # noqa: F401

from src import db


class MultiSeasonTestCase(ApiTestCase):
    def _season(self, name):
        return run(db.create_season(self.db_path, GUILD, name, "123"))

    def _create_tournament(self, **over):
        self._crew(True)
        r = self.client.post("/api/tournaments", headers=self.h("123"),
                             json=self._create_body(**over))
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    def _links(self, tid):
        rows = run(db.get_seasons_for_tournament(self.db_path, tid,
                                                 status=None))
        return [s["id"] for s in rows]

    def _standings(self, season_id):
        r = self.client.get(
            f"/api/seasons/standings?season_id={season_id}",
            headers=self.h("123"))
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    # -- creation picks -----------------------------------------------

    def test_create_picks_one_of_two(self):
        a = self._season("Season A")
        b = self._season("Season B")
        body = self._create_tournament(season_id=b)
        self.assertEqual(self._links(body["id"]), [b])
        self.assertEqual([s["id"] for s in body["seasons"]], [b])

    def test_create_no_pick_links_none(self):
        self._season("Season A")
        self._season("Season B")
        body = self._create_tournament()
        self.assertEqual(self._links(body["id"]), [])
        self.assertEqual(body["seasons"], [])

    def test_create_pick_completed_season_422(self):
        a = self._season("Season A")
        run(db.complete_season(self.db_path, a))
        self._crew(True)
        r = self.client.post("/api/tournaments", headers=self.h("123"),
                             json=self._create_body(season_id=a))
        self.assertEqual(r.status_code, 422, r.text)
        self.assertEqual(r.json()["code"], "season_not_active")

    def test_create_pick_unknown_season_422(self):
        self._crew(True)
        r = self.client.post("/api/tournaments", headers=self.h("123"),
                             json=self._create_body(season_id=99999))
        self.assertEqual(r.status_code, 422, r.text)
        self.assertEqual(r.json()["code"], "season_not_found")

    # -- listing + standings ------------------------------------------

    def test_list_seasons(self):
        a = self._season("Season A")
        self._season("Season B")
        run(db.add_tournament_to_season(self.db_path, a, self.t_open))
        r = self.client.get("/api/seasons", headers=self.h("123"))
        self.assertEqual(r.status_code, 200, r.text)
        seasons = r.json()["seasons"]
        self.assertEqual(len(seasons), 2)
        by_id = {s["id"]: s for s in seasons}
        self.assertEqual(by_id[a]["tournament_count"], 1)
        self.assertEqual(by_id[a]["status"], "active")

    def test_standings_default_is_newest_active(self):
        self._season("Season A")
        b = self._season("Season B")
        run(db.record_season_points(self.db_path, b, self.t_open,
                                    [("123", 1, 100)]))
        r = self.client.get("/api/seasons/standings", headers=self.h("123"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["season"]["id"], b)

    def test_standings_by_id_including_completed(self):
        a = self._season("Season A")
        run(db.record_season_points(self.db_path, a, self.t_open,
                                    [("456", 1, 100)]))
        run(db.complete_season(self.db_path, a))
        body = self._standings(a)
        self.assertEqual(body["season"]["status"], "completed")
        self.assertEqual(body["standings"][0]["discord_id"], "456")
        self.assertEqual(body["standings"][0]["total_points"], 100)

    def test_standings_unknown_season_404(self):
        r = self.client.get("/api/seasons/standings?season_id=99999",
                            headers=self.h("123"))
        self.assertEqual(r.status_code, 404, r.text)
        self.assertEqual(r.json()["code"], "season_not_found")

    # -- correcting the assignment -------------------------------------

    def test_set_season_before_completion(self):
        a = self._season("Season A")
        b = self._season("Season B")
        self._crew(True)
        r = self.client.put(f"/api/tournaments/{self.t_open}/season",
                            headers=self.h("123"), json={"season_id": a})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self._links(self.t_open), [a])
        r = self.client.put(f"/api/tournaments/{self.t_open}/season",
                            headers=self.h("123"), json={"season_id": b})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["season"]["id"], b)
        self.assertEqual(self._links(self.t_open), [b])
        r = self.client.put(f"/api/tournaments/{self.t_open}/season",
                            headers=self.h("123"), json={"season_id": None})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIsNone(r.json()["season"])
        self.assertEqual(self._links(self.t_open), [])

    def test_set_season_requires_crew(self):
        a = self._season("Season A")
        self._crew(False)
        r = self.client.put(f"/api/tournaments/{self.t_open}/season",
                            headers=self.h("123"), json={"season_id": a})
        self.assertEqual(r.status_code, 403, r.text)

    def test_set_season_into_completed_422(self):
        a = self._season("Season A")
        run(db.complete_season(self.db_path, a))
        self._crew(True)
        r = self.client.put(f"/api/tournaments/{self.t_open}/season",
                            headers=self.h("123"), json={"season_id": a})
        self.assertEqual(r.status_code, 422, r.text)
        self.assertEqual(r.json()["code"], "season_not_active")

    def test_points_move_with_assignment_after_completion(self):
        a = self._season("Season A")
        b = self._season("Season B")
        self._crew(True)
        # Verified cards on the seeded tournament: 123 wins, 456 second.
        self.with_tz("123")
        self.with_tz("456")
        tt_id = self._past_tee_time(self.t_open, creator="123",
                                    extra_players=["456"])
        run(db.upsert_scorecard(self.db_path, self.t_open, "123", None,
                                tt_id, [4] * 18, "verified",
                                submitted_by="123"))
        run(db.upsert_scorecard(self.db_path, self.t_open, "456", None,
                                tt_id, [5] * 18, "verified",
                                submitted_by="456"))
        r = self.client.put(f"/api/tournaments/{self.t_open}/season",
                            headers=self.h("123"), json={"season_id": a})
        self.assertEqual(r.status_code, 200, r.text)
        r = self.client.post(f"/api/tournaments/{self.t_open}/complete",
                             headers=self.h("123"))
        self.assertEqual(r.status_code, 200, r.text)
        rows = self._standings(a)["standings"]
        self.assertEqual(
            [(x["discord_id"], x["total_points"]) for x in rows],
            [("123", 100), ("456", 90)])
        self.assertEqual(self._standings(b)["standings"], [])

        # Correct the mistake: move it to B. Points follow.
        r = self.client.put(f"/api/tournaments/{self.t_open}/season",
                            headers=self.h("123"), json={"season_id": b})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["points_removed"], 2)
        self.assertEqual(r.json()["points_awarded"], 2)
        self.assertEqual(self._standings(a)["standings"], [])
        rows = self._standings(b)["standings"]
        self.assertEqual(
            [(x["discord_id"], x["total_points"]) for x in rows],
            [("123", 100), ("456", 90)])

        # And clearing the assignment pulls the points back out.
        r = self.client.put(f"/api/tournaments/{self.t_open}/season",
                            headers=self.h("123"),
                            json={"season_id": None})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self._standings(b)["standings"], [])

    def test_tournament_list_shows_season(self):
        a = self._season("Season A")
        run(db.add_tournament_to_season(self.db_path, a, self.t_open))
        r = self.client.get("/api/tournaments", headers=self.h("123"))
        self.assertEqual(r.status_code, 200, r.text)
        mine = [t for t in r.json() if t["id"] == self.t_open][0]
        self.assertEqual([s["id"] for s in mine["seasons"]], [a])
        self.assertEqual(mine["seasons"][0]["name"], "Season A")


del ApiTestCase

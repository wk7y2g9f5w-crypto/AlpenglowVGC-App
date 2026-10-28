"""Per-round course selection (Cayden 2026-10-28).

Each round of a tournament may play its own course with its own pars
(R1 Torrey Pines North, R2 Torrey Pines South). Rounds resolve their
effective course/pars as: the round's own values when set, else the
tournament's. Creation stores the tournament course/pars on rounds that
don't specify one; a round row with NULL course (pre-feature data, or a
direct db.create_round) still resolves the tournament's at read time.
"""

from test_api import ApiTestCase, run, GUILD

from src import db

NORTH = [4, 4, 3, 4, 5, 4, 4, 3, 5, 5, 4, 3, 4, 4, 3, 4, 5, 4]
SOUTH = [4, 4, 3, 4, 4, 5, 4, 3, 5, 4, 3, 4, 5, 4, 4, 3, 4, 5]
PEBBLE = [4, 5, 4, 4, 3, 5, 3, 4, 4, 4, 4, 3, 4, 5, 4, 4, 3, 5]
SOUTHWIND = [4, 4, 5, 3, 4, 4, 4, 3, 4, 4, 3, 4, 4, 3, 4, 5, 4, 4]

NORTH_NAME = "Torrey Pines North Course"
SOUTH_NAME = "Torrey Pines South Course"


def _csv(pars):
    return ",".join(str(p) for p in pars)


class PerRoundCourseApiTestCase(ApiTestCase):
    def _create_tourney(self, **over):
        self._crew(True)
        body = self._create_body(**over)
        r = self.client.post("/api/tournaments", headers=self.h("123"),
                             json=body)
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    def _two_rounds(self, r1=None, r2=None):
        base = [
            {"start_date": "2027-03-01", "end_date": "2027-03-07"},
            {"start_date": "2027-03-08", "end_date": "2027-03-14"},
        ]
        for r, extra in zip(base, (r1, r2)):
            if extra:
                r.update(extra)
        return base

    # -- creation ------------------------------------------------------

    def test_create_mixed_round_courses(self):
        # Tournament course North; R1 omits a course (inherits), R2
        # plays South.
        t = self._create_tourney(
            course=NORTH_NAME, start_date="2027-03-01",
            end_date="2027-03-14",
            rounds=self._two_rounds({}, {"course": SOUTH_NAME}))
        self.assertEqual(t["course"], NORTH_NAME)
        self.assertEqual(t["pars"], NORTH)
        self.assertEqual(t["rounds"][0]["course"], NORTH_NAME)
        self.assertEqual(t["rounds"][0]["pars"], NORTH)
        self.assertEqual(t["rounds"][1]["course"], SOUTH_NAME)
        self.assertEqual(t["rounds"][1]["pars"], SOUTH)
        # Stored explicitly on both round rows.
        rows = run(db.list_rounds(self.db_path, t["id"]))
        self.assertEqual(rows[0]["course"], NORTH_NAME)
        self.assertEqual(rows[0]["pars"], _csv(NORTH))
        self.assertEqual(rows[1]["course"], SOUTH_NAME)
        self.assertEqual(rows[1]["pars"], _csv(SOUTH))

    def test_create_both_rounds_explicit_courses(self):
        t = self._create_tourney(
            course=NORTH_NAME, start_date="2027-03-01",
            end_date="2027-03-14",
            rounds=self._two_rounds({"course": SOUTH_NAME},
                                    {"course": NORTH_NAME}))
        self.assertEqual(t["rounds"][0]["course"], SOUTH_NAME)
        self.assertEqual(t["rounds"][0]["pars"], SOUTH)
        self.assertEqual(t["rounds"][1]["course"], NORTH_NAME)
        self.assertEqual(t["rounds"][1]["pars"], NORTH)

    def test_create_rounds_default_to_tournament_course(self):
        t = self._create_tourney(
            start_date="2027-03-01", end_date="2027-03-14",
            rounds=self._two_rounds({}, {}))
        for rnd in t["rounds"]:
            self.assertEqual(rnd["course"], "Pebble Beach Golf Links")
            self.assertEqual(rnd["pars"], PEBBLE)
        rows = run(db.list_rounds(self.db_path, t["id"]))
        for row in rows:
            self.assertEqual(row["course"], "Pebble Beach Golf Links")
            self.assertEqual(row["pars"], _csv(PEBBLE))

    def test_create_without_rounds_payload_stores_tournament_course(self):
        t = self._create_tourney()
        self.assertEqual(t["num_rounds"], 1)
        self.assertEqual(t["rounds"][0]["course"],
                         "Pebble Beach Golf Links")
        self.assertEqual(t["rounds"][0]["pars"], PEBBLE)
        rows = run(db.list_rounds(self.db_path, t["id"]))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["course"], "Pebble Beach Golf Links")
        self.assertEqual(rows[0]["pars"], _csv(PEBBLE))

    def test_create_unknown_round_course_422(self):
        self._crew(True)
        r = self.client.post(
            "/api/tournaments", headers=self.h("123"),
            json=self._create_body(
                course=NORTH_NAME, start_date="2027-03-01",
                end_date="2027-03-14",
                rounds=self._two_rounds({"course": NORTH_NAME},
                                        {"course": "Nowhere Golf Club"})))
        self.assertEqual(r.status_code, 422, r.text)
        self.assertIn("Unknown course", r.json()["detail"])
        # Nothing was created.
        names = [x["name"] for x in
                 run(db.list_tournaments(self.db_path, GUILD))]
        self.assertNotIn("App Open", names)

    def test_create_match_single_round_with_course(self):
        t = self._create_tourney(
            format="match", course=NORTH_NAME,
            rounds=[{"course": SOUTH_NAME}])
        self.assertEqual(t["num_rounds"], 1)
        self.assertEqual(t["rounds"][0]["course"], SOUTH_NAME)
        self.assertEqual(t["rounds"][0]["pars"], SOUTH)

    # -- legacy fallback -------------------------------------------------

    def test_legacy_null_course_round_resolves_tournament(self):
        tid = run(db.create_tournament(
            self.db_path, GUILD, "Legacy", "stroke", 18,
            "Pebble Beach Golf Links", _csv(PEBBLE), None, "1",
            start_date="2027-03-01", end_date="2027-03-14"))
        # A round row with NULL course/pars (pre-feature shape).
        run(db.create_round(self.db_path, tid, 2))
        row = run(db.get_round(self.db_path, tid, 2))
        self.assertIsNone(row["course"])
        self.assertIsNone(row["pars"])
        r = self.client.get("/api/tournaments", headers=self.h("123"))
        self.assertEqual(r.status_code, 200, r.text)
        t = next(x for x in r.json() if x["id"] == tid)
        self.assertEqual(t["rounds"][1]["course"],
                         "Pebble Beach Golf Links")
        self.assertEqual(t["rounds"][1]["pars"], PEBBLE)

    # -- round PATCH -----------------------------------------------------

    def _played_tournament(self):
        """Two Pebble rounds (future windows); player 123 has a verified
        all-4s card (gross 72) on round 2."""
        t = self._create_tourney(
            start_date="2027-03-01", end_date="2027-03-14",
            rounds=self._two_rounds({}, {}))
        tid = t["id"]
        tt_id = run(db.create_tee_time(
            self.db_path, tid, "r2 flight", "2027-03-08T15:30:00+00:00",
            4, "123", None, round_number=2))
        run(db._execute(
            self.db_path,
            "UPDATE tee_times SET witness_rule = 0 WHERE id = ?",
            (tt_id,)))
        run(db.join_tee_time(self.db_path, tt_id, "123"))
        run(db.upsert_scorecard(
            self.db_path, tid, "123", None, tt_id, [4] * 18, "verified",
            submitted_by="123", round_number=2))
        return t, tt_id

    def _get_card(self, tt_id):
        r = self.client.get(f"/api/tee-times/{tt_id}/scorecard",
                            headers=self.h("123"),
                            params={"round_number": 2})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["card"]

    def test_patch_round_course_moves_pars_not_gross(self):
        t, tt_id = self._played_tournament()
        tid = t["id"]
        card = self._get_card(tt_id)
        self.assertEqual(card["total"], 72)
        self.assertEqual(card["to_par"], 0)  # Pebble is par 72

        self._crew(True)
        r = self.client.patch(
            f"/api/tournaments/{tid}/rounds/2", headers=self.h("123"),
            json={"course": "TPC Southwind"})
        self.assertEqual(r.status_code, 200, r.text)
        rnd = r.json()["rounds"][1]
        self.assertEqual(rnd["course"], "TPC Southwind")
        self.assertEqual(rnd["pars"], SOUTHWIND)

        # Scorecard par display follows the new course; gross stands.
        card = self._get_card(tt_id)
        self.assertEqual(card["total"], 72)
        self.assertEqual(card["to_par"], 2)  # Southwind is par 70

        # Leaderboard to-par (cumulative and per-round) follows too.
        r = self.client.get(f"/api/tournaments/{tid}/leaderboard",
                            headers=self.h("123"))
        self.assertEqual(r.status_code, 200, r.text)
        lb = r.json()
        self.assertEqual(lb["rounds"][1]["course"], "TPC Southwind")
        row = lb["standings"][0]
        self.assertEqual(row["total"], 72)
        self.assertEqual(row["to_par"], 2)
        detail = {d["round_number"]: d for d in row["rounds"]}
        self.assertEqual(detail[2]["to_par"], 2)
        self.assertIsNone(detail[1]["to_par"])

        # Tee-time JSON exposes the round's effective course/pars.
        r = self.client.get(f"/api/tournaments/{tid}/tee-times",
                            headers=self.h("123"))
        self.assertEqual(r.status_code, 200, r.text)
        tt = r.json()[0]
        self.assertEqual(tt["course"], "TPC Southwind")
        self.assertEqual(tt["pars"], SOUTHWIND)

        # Round history resolves the round's course/pars.
        r = self.client.get("/api/players/123/rounds",
                            headers=self.h("123"))
        self.assertEqual(r.status_code, 200, r.text)
        entry = next(x for x in r.json()["rounds"]
                     if x["tournament_id"] == tid)
        self.assertEqual(entry["course"], "TPC Southwind")
        self.assertEqual(entry["pars"], SOUTHWIND)
        self.assertEqual(entry["total"], 72)
        self.assertEqual(entry["to_par"], 2)

    def test_patch_round_without_course_keeps_course(self):
        t, _ = self._played_tournament()
        tid = t["id"]
        self._crew(True)
        r = self.client.patch(
            f"/api/tournaments/{tid}/rounds/2", headers=self.h("123"),
            json={"course": "TPC Southwind"})
        self.assertEqual(r.status_code, 200, r.text)
        r = self.client.patch(
            f"/api/tournaments/{tid}/rounds/2", headers=self.h("123"),
            json={"wind_strength": "low"})
        self.assertEqual(r.status_code, 200, r.text)
        rnd = r.json()["rounds"][1]
        self.assertEqual(rnd["course"], "TPC Southwind")
        self.assertEqual(rnd["pars"], SOUTHWIND)
        self.assertEqual(rnd["wind_strength"], "low")

    def test_patch_round_unknown_course_422(self):
        t, _ = self._played_tournament()
        self._crew(True)
        r = self.client.patch(
            f"/api/tournaments/{t['id']}/rounds/2", headers=self.h("123"),
            json={"course": "Nowhere Golf Club"})
        self.assertEqual(r.status_code, 422, r.text)
        self.assertIn("Unknown course", r.json()["detail"])
        row = run(db.get_round(self.db_path, t["id"], 2))
        self.assertEqual(row["course"], "Pebble Beach Golf Links")

    # -- single-round behaviour unchanged --------------------------------

    def test_single_round_leaderboard_unchanged(self):
        t = self._create_tourney()
        tid = t["id"]
        tt_id = run(db.create_tee_time(
            self.db_path, tid, "solo flight", "2026-10-03T15:30:00+00:00",
            4, "123", None, round_number=1))
        run(db._execute(
            self.db_path,
            "UPDATE tee_times SET witness_rule = 0 WHERE id = ?",
            (tt_id,)))
        run(db.join_tee_time(self.db_path, tt_id, "123"))
        run(db.upsert_scorecard(
            self.db_path, tid, "123", None, tt_id, [4] * 18, "verified",
            submitted_by="123", round_number=1))
        r = self.client.get(f"/api/tournaments/{tid}/leaderboard",
                            headers=self.h("123"))
        self.assertEqual(r.status_code, 200, r.text)
        lb = r.json()
        self.assertEqual(lb["course"], "Pebble Beach Golf Links")
        self.assertEqual(lb["rounds"][0]["course"],
                         "Pebble Beach Golf Links")
        self.assertEqual(lb["rounds"][0]["pars"], PEBBLE)
        self.assertEqual(lb["standings"][0]["total"], 72)
        self.assertEqual(lb["standings"][0]["to_par"], 0)


del ApiTestCase

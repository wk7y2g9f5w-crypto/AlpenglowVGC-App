"""Tests for the Golf+ course picker (src/golfplus_courses.py)."""
import unittest

from src import golfplus_courses
from src.golfplus_courses import COURSES, course_choices, course_pars


class TestCourseChoices(unittest.TestCase):
    def test_empty_returns_first_25(self):
        got = course_choices("")
        self.assertEqual(got, [(c, c) for c in COURSES[:25]])

    def test_none_returns_first_25(self):
        self.assertEqual(course_choices(None), [(c, c) for c in COURSES[:25]])

    def test_case_insensitive_substring(self):
        got = course_choices("pebble")
        self.assertEqual(got, [("Pebble Beach Golf Links", "Pebble Beach Golf Links")])

    def test_uppercase_query_matches(self):
        got = course_choices("TPC")
        names = [n for n, _ in got]
        self.assertEqual(
            names, ["TPC Sawgrass", "TPC Scottsdale", "TPC Southwind"]
        )

    def test_partial_word_matches(self):
        got = course_choices("kiawah")
        self.assertEqual(
            got, [("The Ocean Course at Kiawah Island",
                   "The Ocean Course at Kiawah Island")]
        )

    def test_no_match_returns_empty(self):
        self.assertEqual(course_choices("zzzznope"), [])

    def test_whitespace_only_behaves_like_empty(self):
        self.assertEqual(course_choices("   "), course_choices(""))

    def test_limit_respected(self):
        # "e" matches many courses; limit caps the results.
        got = course_choices("e", limit=5)
        self.assertEqual(len(got), 5)
        self.assertTrue(all(isinstance(n, str) and isinstance(v, str)
                            for n, v in got))

    def test_list_is_nontrivial(self):
        # Real licensed courses only — fictional Golf+ originals (Alpine,
        # Castle Hill, Links, The Cliffs) were removed per Cayden 2026-09-25.
        self.assertGreaterEqual(len(COURSES), 15)
        for fictional in ("Alpine", "Castle Hill", "Links", "The Cliffs"):
            self.assertNotIn(fictional, COURSES)


class TestCoursePars(unittest.TestCase):
    def test_unknown_course_returns_none(self):
        self.assertIsNone(course_pars("Not A Real Course", 18))
        self.assertIsNone(course_pars("", 18))
        self.assertIsNone(course_pars(None, 18))

    def test_lookup_and_slicing(self):
        fake = [4, 5, 3, 4, 4, 5, 3, 4, 4,
                4, 3, 5, 4, 4, 3, 4, 5, 4]
        old = golfplus_courses.COURSE_PARS
        golfplus_courses.COURSE_PARS = {"Fake CC": fake}
        try:
            self.assertEqual(course_pars("Fake CC", 18), fake)
            self.assertEqual(course_pars("  Fake CC  ", 18), fake)  # whitespace tolerant
            self.assertEqual(course_pars("Fake CC", 9), fake[:9])   # front 9
        finally:
            golfplus_courses.COURSE_PARS = old

    def test_fuzzy_substring_match(self):
        old = golfplus_courses.COURSE_PARS
        golfplus_courses.COURSE_PARS = {
            "Pebble Beach Golf Links": [4] * 18,
            "TPC Scottsdale": [4] * 18,
            "TPC Sawgrass": [4] * 18,
        }
        try:
            # Unambiguous substring, case-insensitive.
            self.assertEqual(course_pars("pebble", 18), [4] * 18)
            self.assertEqual(course_pars("SCOTTSDALE", 9), [4] * 9)
            # Ambiguous ("tpc" matches two) -> None, not a guess.
            self.assertIsNone(course_pars("tpc", 18))
            # No match -> None.
            self.assertIsNone(course_pars("augusta", 18))
        finally:
            golfplus_courses.COURSE_PARS = old

    def test_database_shape(self):
        # Every researched entry: exact COURSES key, 18 holes, pars 3-6.
        # Vacuous until research lands; then it guards the data.
        for name, pars in golfplus_courses.COURSE_PARS.items():
            self.assertIn(name, COURSES, f"{name} not in COURSES")
            self.assertEqual(len(pars), 18, f"{name}: expected 18 pars")
            for p in pars:
                self.assertIsInstance(p, int)
                self.assertGreaterEqual(p, 3)
                self.assertLessEqual(p, 6)


if __name__ == "__main__":
    unittest.main()

"""Tests for career_stats in scoring_logic (pure, no Discord/DB needed).

Run with:  .venv/bin/python -m unittest discover -s tests
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import scoring_logic as sl

PARS_9 = [4, 4, 3, 4, 5, 4, 3, 4, 4]  # par 35


class CareerStatsTest(unittest.TestCase):
    def test_single_round_full_breakdown(self):
        # birdie, par, bogey, double, eagle-ish, then pars
        holes = [3, 4, 4, 6, 3, 4, 3, 4, 4]
        s = sl.career_stats([{"holes": holes, "pars": PARS_9}])
        self.assertEqual(s["rounds_played"], 1)
        self.assertEqual(s["best_total"], sum(holes))
        self.assertEqual(s["best_to_par"], sum(holes) - 35)
        self.assertAlmostEqual(s["avg_total"], sum(holes))
        # diffs: -1, 0, +1, +2, -2, 0, 0, 0, 0
        self.assertEqual(s["birdies_or_better"], 2)
        self.assertEqual(s["pars_made"], 5)
        self.assertEqual(s["bogeys"], 1)
        self.assertEqual(s["doubles_or_worse"], 1)
        # longest run of score <= par: holes 5-9 -> 5
        self.assertEqual(s["best_par_streak"], 5)

    def test_par_streak_broken_by_over_par(self):
        pars = [4, 4, 4, 4, 4]
        holes = [4, 4, 5, 4, 4]  # streaks of 2, 2 -> best 2
        s = sl.career_stats([{"holes": holes, "pars": pars}])
        self.assertEqual(s["best_par_streak"], 2)

    def test_par_streak_counts_birdies_too(self):
        pars = [4, 4, 4, 4]
        holes = [3, 3, 3, 5]  # under-par still extends the streak
        s = sl.career_stats([{"holes": holes, "pars": pars}])
        self.assertEqual(s["best_par_streak"], 3)
        self.assertEqual(s["birdies_or_better"], 3)

    def test_par_streak_zero_when_never_at_par(self):
        pars = [4, 4, 4]
        holes = [5, 6, 5]
        s = sl.career_stats([{"holes": holes, "pars": pars}])
        self.assertEqual(s["best_par_streak"], 0)
        self.assertEqual(s["doubles_or_worse"], 1)
        self.assertEqual(s["bogeys"], 2)

    def test_best_streak_across_rounds(self):
        pars = [4, 4, 4, 4]
        r1 = {"holes": [4, 4, 5, 5], "pars": pars}  # best streak 2
        r2 = {"holes": [5, 4, 4, 4], "pars": pars}  # best streak 3
        s = sl.career_stats([r1, r2])
        self.assertEqual(s["rounds_played"], 2)
        self.assertEqual(s["best_par_streak"], 3)

    def test_no_pars(self):
        s = sl.career_stats([{"holes": [4, 5, 4], "pars": None},
                             {"holes": [3, 3, 4], "pars": None}])
        self.assertEqual(s["rounds_played"], 2)
        self.assertEqual(s["best_total"], 10)
        self.assertIsNone(s["best_to_par"])
        self.assertAlmostEqual(s["avg_total"], 11.5)
        self.assertEqual(s["birdies_or_better"], 0)
        self.assertEqual(s["pars_made"], 0)
        self.assertEqual(s["bogeys"], 0)
        self.assertEqual(s["doubles_or_worse"], 0)
        self.assertIsNone(s["best_par_streak"])

    def test_mismatched_pars_treated_as_unknown(self):
        s = sl.career_stats([{"holes": [4, 4, 4], "pars": [4, 4]}])
        self.assertEqual(s["best_total"], 12)
        self.assertIsNone(s["best_to_par"])
        self.assertIsNone(s["best_par_streak"])
        self.assertEqual(s["pars_made"], 0)

    def test_mixed_pars_and_no_pars_rounds(self):
        s = sl.career_stats([{"holes": [4, 4], "pars": [4, 4]},
                             {"holes": [5, 5], "pars": None}])
        self.assertEqual(s["rounds_played"], 2)
        self.assertEqual(s["best_total"], 8)
        self.assertEqual(s["best_to_par"], 0)
        self.assertEqual(s["pars_made"], 2)
        self.assertEqual(s["best_par_streak"], 2)

    def test_best_to_par_takes_minimum(self):
        pars = [4, 4]
        r1 = {"holes": [5, 5], "pars": pars}  # +2
        r2 = {"holes": [3, 4], "pars": pars}  # -1
        s = sl.career_stats([r1, r2])
        self.assertEqual(s["best_to_par"], -1)
        self.assertEqual(s["best_total"], 7)

    def test_empty_rounds(self):
        s = sl.career_stats([])
        self.assertEqual(s["rounds_played"], 0)
        self.assertIsNone(s["best_total"])
        self.assertIsNone(s["best_to_par"])
        self.assertIsNone(s["avg_total"])
        self.assertEqual(s["birdies_or_better"], 0)
        self.assertIsNone(s["best_par_streak"])


if __name__ == "__main__":
    unittest.main()

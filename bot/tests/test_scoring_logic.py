"""Unit tests for the pure scoring logic.

Run with:  python -m unittest discover -s tests
       or:  python -m pytest tests/
No Discord connection, no discord.py import required.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.scoring_logic import (
    best_ball_holes,
    best_ball_total,
    format_date,
    format_date_range,
    format_to_par,
    match_records,
    parse_date,
    parse_hole_scores,
    parse_names_list,
    parse_pars,
    rank_match_records,
    rank_scramble,
    rank_stroke,
    split_score_cards,
    to_par,
    total,
    validate_date_range,
)


class TestParseHoleScores(unittest.TestCase):
    def test_valid_nine(self):
        self.assertEqual(
            parse_hole_scores("4,5,3,4,4,5,3,4,5", 9),
            [4, 5, 3, 4, 4, 5, 3, 4, 5],
        )

    def test_valid_eighteen_with_spaces(self):
        text = "4, 5, 3, 4, 4, 5, 3, 4, 5, 4, 5, 3, 4, 4, 5, 3, 4, 5"
        self.assertEqual(len(parse_hole_scores(text, 18)), 18)

    def test_wrong_count_raises(self):
        with self.assertRaises(ValueError):
            parse_hole_scores("4,5,3", 9)

    def test_too_many_raises(self):
        with self.assertRaises(ValueError):
            parse_hole_scores("4,5,3,4,4,5,3,4,5,4", 9)

    def test_non_numeric_raises(self):
        with self.assertRaises(ValueError):
            parse_hole_scores("4,5,bogey,4,4,5,3,4,5", 9)

    def test_zero_raises(self):
        with self.assertRaises(ValueError):
            parse_hole_scores("4,5,0,4,4,5,3,4,5", 9)

    def test_sixteen_raises(self):
        with self.assertRaises(ValueError):
            parse_hole_scores("4,5,16,4,4,5,3,4,5", 9)

    def test_semicolons_accepted(self):
        self.assertEqual(
            parse_hole_scores("4;5;3;4;4;5;3;4;5", 9),
            [4, 5, 3, 4, 4, 5, 3, 4, 5],
        )


class TestParsePars(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(parse_pars("4,4,3,5,4,3,4,4,5", 9),
                         [4, 4, 3, 5, 4, 3, 4, 4, 5])

    def test_bad_par_raises(self):
        with self.assertRaises(ValueError):
            parse_pars("4,4,2,5,4,3,4,4,5", 9)

    def test_wrong_count_raises(self):
        with self.assertRaises(ValueError):
            parse_pars("4,4,3", 9)


class TestTotals(unittest.TestCase):
    def test_total(self):
        self.assertEqual(total([4, 5, 3]), 12)

    def test_to_par_under(self):
        self.assertEqual(to_par(70, [4] * 18), -2)

    def test_to_par_even(self):
        self.assertEqual(to_par(72, [4] * 18), 0)

    def test_to_par_over(self):
        self.assertEqual(to_par(80, [4] * 18), 8)

    def test_to_par_none_without_pars(self):
        self.assertIsNone(to_par(70, None))

    def test_format_to_par(self):
        self.assertEqual(format_to_par(-2), "-2")
        self.assertEqual(format_to_par(0), "E")
        self.assertEqual(format_to_par(3), "+3")
        self.assertEqual(format_to_par(None), "")


class TestBestBall(unittest.TestCase):
    def test_per_hole_min(self):
        cards = [[4, 5, 3], [5, 4, 4], [6, 6, 2]]
        self.assertEqual(best_ball_holes(cards), [4, 4, 2])

    def test_total(self):
        cards = [[4, 5, 3], [5, 4, 4]]
        self.assertEqual(best_ball_total(cards), 11)

    def test_single_card(self):
        self.assertEqual(best_ball_holes([[4, 5, 3]]), [4, 5, 3])

    def test_mismatched_lengths_raise(self):
        with self.assertRaises(ValueError):
            best_ball_holes([[4, 5, 3], [4, 5]])

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            best_ball_holes([])


class TestMatchRecords(unittest.TestCase):
    def test_win_loss(self):
        rec = match_records([
            {"player1": "a", "player2": "b", "winner": "player1"},
        ])
        self.assertEqual(rec["a"], {"w": 1, "l": 0, "t": 0})
        self.assertEqual(rec["b"], {"w": 0, "l": 1, "t": 0})

    def test_tie(self):
        rec = match_records([
            {"player1": "a", "player2": "b", "winner": "tie"},
        ])
        self.assertEqual(rec["a"]["t"], 1)
        self.assertEqual(rec["b"]["t"], 1)

    def test_accumulates(self):
        rec = match_records([
            {"player1": "a", "player2": "b", "winner": "player1"},
            {"player1": "a", "player2": "c", "winner": "player2"},
            {"player1": "b", "player2": "c", "winner": "tie"},
        ])
        self.assertEqual(rec["a"], {"w": 1, "l": 1, "t": 0})
        self.assertEqual(rec["b"], {"w": 0, "l": 1, "t": 1})
        self.assertEqual(rec["c"], {"w": 1, "l": 0, "t": 1})

    def test_ranking_order(self):
        rec = {
            "b": {"w": 1, "l": 0, "t": 0},
            "a": {"w": 2, "l": 1, "t": 0},
            "c": {"w": 2, "l": 0, "t": 1},
        }
        ordered = [pid for pid, _ in rank_match_records(rec)]
        self.assertEqual(ordered, ["c", "a", "b"])


class TestRankStroke(unittest.TestCase):
    def test_sorted_ascending(self):
        entries = [{"total": 75}, {"total": 68}, {"total": 72}]
        self.assertEqual([e["total"] for e in rank_stroke(entries)], [68, 72, 75])


class TestRankScramble(unittest.TestCase):
    def test_sorted_ascending(self):
        entries = [
            {"name": "Eagles", "total": 70},
            {"name": "Bogeys", "total": 64},
            {"name": "Pars", "total": 68},
        ]
        self.assertEqual(
            [e["name"] for e in rank_scramble(entries)],
            ["Bogeys", "Pars", "Eagles"],
        )

    def test_tie_breaks_by_name(self):
        entries = [
            {"name": "Zebras", "total": 65},
            {"name": "Aces", "total": 65},
        ]
        self.assertEqual(
            [e["name"] for e in rank_scramble(entries)], ["Aces", "Zebras"]
        )


class TestDateValidation(unittest.TestCase):
    def test_parse_valid(self):
        d = parse_date("2026-10-03")
        self.assertEqual((d.year, d.month, d.day), (2026, 10, 3))

    def test_parse_invalid_month_raises(self):
        with self.assertRaises(ValueError):
            parse_date("2026-13-01")

    def test_parse_nonexistent_day_raises(self):
        with self.assertRaises(ValueError):
            parse_date("2026-02-30")

    def test_parse_wrong_format_raises(self):
        with self.assertRaises(ValueError):
            parse_date("10/03/2026")

    def test_validate_range_ok(self):
        start, end = validate_date_range("2026-10-03", "2026-10-10")
        self.assertLess(start, end)

    def test_validate_single_day_ok(self):
        start, end = validate_date_range("2026-10-03", "2026-10-03")
        self.assertEqual(start, end)

    def test_validate_end_before_start_raises(self):
        with self.assertRaises(ValueError):
            validate_date_range("2026-10-10", "2026-10-03")

    def test_format_date(self):
        self.assertEqual(format_date("2026-10-03"), "Oct 3")
        self.assertEqual(format_date(None), "TBD")
        self.assertEqual(format_date("garbage"), "TBD")

    def test_format_date_range(self):
        self.assertEqual(
            format_date_range("2026-10-03", "2026-10-10"), "Oct 3 – Oct 10"
        )
        self.assertEqual(format_date_range("2026-10-03", "2026-10-03"), "Oct 3")
        self.assertEqual(format_date_range(None, None), "TBD")
        self.assertEqual(
            format_date_range("2026-10-10", "2026-10-03"), "TBD"
        )


class TestParseNamesList(unittest.TestCase):
    def test_comma_separated(self):
        self.assertEqual(parse_names_list("Alice, Bob"), ["Alice", "Bob"])

    def test_names_with_spaces_kept(self):
        self.assertEqual(
            parse_names_list("Kristian Mabe, Tank The Frank"),
            ["Kristian Mabe", "Tank The Frank"],
        )

    def test_semicolons_and_newlines(self):
        self.assertEqual(
            parse_names_list("Alice;Bob\nCharlie"), ["Alice", "Bob", "Charlie"]
        )

    def test_mentions_kept(self):
        self.assertEqual(parse_names_list("<@123>, Bob"), ["<@123>", "Bob"])

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            parse_names_list("   ")

    def test_cap_raises(self):
        with self.assertRaises(ValueError):
            parse_names_list(",".join(f"P{i}" for i in range(9)))


class TestSplitScoreCards(unittest.TestCase):
    def test_split(self):
        self.assertEqual(
            split_score_cards("4,4,4 ; 5,5,5"), ["4,4,4", "5,5,5"]
        )

    def test_single(self):
        self.assertEqual(split_score_cards("4,4,4"), ["4,4,4"])

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            split_score_cards("  ;  ")


if __name__ == "__main__":
    unittest.main()

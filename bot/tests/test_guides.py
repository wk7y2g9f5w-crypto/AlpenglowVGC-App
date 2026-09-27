"""Tests for the command guide content (src/guide_content.py).

The player guide (#command-guide) must not leak admin-only commands;
the crew guide (#crew-guide) must include them.
"""
import unittest

from src import guide_content as gc

ADMIN_ONLY_MARKERS = [
    "tournament create",
    "verify_score",
    "correct_score",
    "/dq",
    "confirm_match",
    "season create",
    "tee_sheet",
]


class TestGuideContent(unittest.TestCase):
    def test_player_guide_has_no_admin_commands(self):
        text = gc.guide_text(gc.PLAYER_COMMANDS).lower()
        for marker in ADMIN_ONLY_MARKERS:
            self.assertNotIn(
                marker.lower(), text,
                f"player guide must not mention admin-only '{marker}'",
            )

    def test_crew_guide_has_admin_commands(self):
        text = gc.guide_text(gc.CREW_COMMANDS).lower()
        for marker in ADMIN_ONLY_MARKERS:
            self.assertIn(
                marker.lower(), text,
                f"crew guide must mention '{marker}'",
            )

    def test_crew_is_player_plus_admin(self):
        self.assertEqual(
            gc.CREW_COMMANDS, gc.PLAYER_COMMANDS + gc.ADMIN_COMMANDS
        )

    def test_player_guide_covers_core_flows(self):
        text = gc.guide_text(gc.PLAYER_COMMANDS)
        for cmd in ("/register", "/tee_time create", "/submit_score",
                    "/leaderboard", "/sidequest log", "/stats",
                    "/season standings"):
            self.assertIn(cmd, text)

    def test_grouped_preserves_category_order(self):
        groups = gc.grouped(gc.PLAYER_COMMANDS)
        cats = [c for c, _ in groups]
        self.assertEqual(cats, sorted(set(cats), key=cats.index))  # no dupes
        total = sum(len(entries) for _, entries in groups)
        self.assertEqual(total, len(gc.PLAYER_COMMANDS))

    def test_entries_have_one_liners(self):
        for cat, cmd, detail in gc.CREW_COMMANDS:
            self.assertTrue(cat and cmd.startswith("/"),
                            f"bad entry: {(cat, cmd)}")
            self.assertGreater(len(detail), 10, f"one-liner too short: {cmd}")


if __name__ == "__main__":
    unittest.main()

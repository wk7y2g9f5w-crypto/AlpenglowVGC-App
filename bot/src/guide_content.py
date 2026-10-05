"""Curated command reference for the auto-posted guide channels.

Pure data — no discord import, so it's unit-testable. The guides cog
(src/cogs/guides.py) renders these into embeds for #command-guide
(players) and #crew-guide (crew).

Each entry is (category, command, one-liner). One-liners describe actual
bot behavior — keep them accurate when commands change.
"""

# Player-safe commands: shown in #command-guide.
PLAYER_COMMANDS: list[tuple[str, str, str]] = [
    ("Tournaments",
     "/register",
     "Sign up for a tournament (or tap ✅ Register on the announcement) — "
     "requires your timezone set via /setup first."),
    ("Tournaments",
     "/unregister",
     "Withdraw from a tournament."),
    ("Tournaments",
     "/roster",
     "See who's registered for a tournament."),
    ("Tournaments",
     "/tournament list",
     "Browse tournaments — format, dates, status. Play in none, some, or all."),
    ("Tee times",
     "/tee_time create",
     "Book a tee time (your local time — set it with /set_timezone); you're added automatically."),
    ("Tee times",
     "/tee_times",
     "See open tee times with Join buttons."),
    ("Tee times",
     "/tee_time request",
     "Ask to join someone's tee time — the creator approves it."),
    ("Tee times",
     "/tee_time leave",
     "Leave your tee time."),
    ("Scoring",
     "/submit_score",
     "Tap in hole-by-hole scores with the on-screen buttons (custom entry "
     "for blowups); enter your own card or a playing partner's. Unlocks "
     "after your tee time; resubmitting updates the card."),
    ("Scoring",
     "/my_score",
     "Show your submitted scorecard."),
    ("Scoring",
     "/scorecard",
     "Look at another player's scorecard."),
    ("Scoring",
     "/leaderboard",
     "Show the live leaderboard for a tournament."),
    ("Teams & matches",
     "/team create|add|remove",
     "Manage your team — best ball (2–4 players), alt shot (exactly 2), "
     "scramble (2–4)."),
    ("Teams & matches",
     "/team leave|list",
     "Leave your team, or list the teams in a tournament."),
    ("Teams & matches",
     "/report_match",
     "Report a match-play result vs an opponent; they confirm it with a button."),
    ("Teams & matches",
     "/matches",
     "List recent match results."),
    ("Range",
     "/sidequest log",
     "Log a casual round with your buddies (stroke, alt shot, or scramble)."),
    ("Range",
     "/sidequest records",
     "Range history plus personal and course bests per format."),
    ("Profile & seasons",
     "/setup",
     "New-player setup: pick your timezone (required before registering) "
     "and link your Golf+ username. Do this first!"),
    ("Profile & seasons",
     "/set_timezone",
     "Set your timezone so tee-time times are read as your local time."),
    ("Profile & seasons",
     "/link_golfplus",
     "Link your Golf+ username so it shows next to your name on boards and rosters."),
    ("Profile & seasons",
     "/unlink_golfplus",
     "Remove your linked Golf+ username."),
    ("Profile & seasons",
     "/stats",
     "Career stats: rounds, best/average scores, birdies, par streaks. "
     "Defaults to you; tag someone to see theirs."),
    ("Profile & seasons",
     "/season standings",
     "The season-long points race across tournaments."),
    ("Profile & seasons",
     "/season list",
     "List the seasons in this server."),
]

# Admin/crew-only commands: shown in #crew-guide alongside everything above.
ADMIN_COMMANDS: list[tuple[str, str, str]] = [
    ("Running tournaments",
     "/tournament create",
     "Create a tournament: format, holes, course (with picker), dates, "
     "Golf+ tee/pin/wind/green settings, optional pars. Posts the announcement."),
    ("Running tournaments",
     "/tournament start",
     "Open play and post the pinned live leaderboard."),
    ("Running tournaments",
     "/tournament complete",
     "Close the tournament, post final standings, award season points."),
    ("Running tournaments",
     "/verify_score",
     "Verify a pending solo-round scorecard by its card ID."),
    ("Running tournaments",
     "/correct_score",
     "Fix a hole score on someone's card."),
    ("Running tournaments",
     "/dq",
     "Disqualify a player: removes their scores and registration."),
    ("Running tournaments",
     "/confirm_match",
     "Admin override to confirm a reported match result."),
    ("Seasons & boards",
     "/season create",
     "Start a new season (one active per server)."),
    ("Seasons & boards",
     "/season add_tournament",
     "Count a tournament toward the active season — retro-awards points "
     "if it's already complete."),
    ("Seasons & boards",
     "/season complete",
     "Close the season and post final standings."),
    ("Seasons & boards",
     "/tee_sheet post",
     "Post the auto-updating registration + tee-time boards (must run in #tee-sheet)."),
    ("Seasons & boards",
     "/tee_sheet refresh",
     "Refresh the Tee Sheet boards right now."),
    ("Seasons & boards",
     "/guide refresh",
     "Re-post the command guides in #command-guide and #crew-guide."),
]

# Full crew reference: everything players see, plus admin-only commands.
CREW_COMMANDS: list[tuple[str, str, str]] = PLAYER_COMMANDS + ADMIN_COMMANDS


def grouped(
    commands: list[tuple[str, str, str]],
) -> list[tuple[str, list[tuple[str, str]]]]:
    """Group (category, command, detail) entries by category, order preserved."""
    cats: list[str] = []
    by_cat: dict[str, list[tuple[str, str]]] = {}
    for cat, cmd, detail in commands:
        if cat not in by_cat:
            by_cat[cat] = []
            cats.append(cat)
        by_cat[cat].append((cmd, detail))
    return [(cat, by_cat[cat]) for cat in cats]


def guide_text(commands: list[tuple[str, str, str]]) -> str:
    """Plain-text rendering of a command list (used by tests)."""
    lines = []
    for cat, cmd, detail in commands:
        lines.append(f"{cmd} — {detail}")
    return "\n".join(lines)

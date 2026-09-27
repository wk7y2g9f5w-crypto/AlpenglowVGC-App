"""Pure scoring logic for the Golf+ tournament bot.

Stdlib only — no discord.py, no database. Safe to unit-test standalone
without a Discord connection.
"""

import re
import time
from datetime import datetime, timezone
from typing import Iterable, Optional


# Golf+ round settings stored on each tournament.
TEE_LABELS = {"front": "Front", "middle": "Middle", "back": "Back"}
PIN_LABELS = {"black": "Black", "white": "White", "red": "Red"}
WIND_LABELS = {"low": "Low", "moderate": "Moderate", "severe": "Severe"}
GREEN_LABELS = {"veryfast": "Very Fast", "pro": "Pro"}


def format_settings(t) -> str:
    """One-line summary of a tournament's round settings.

    Takes a tournament row (dict-like); unknown values pass through raw
    so older or hand-edited rows never crash rendering.
    """
    tee = TEE_LABELS.get(t.get("tee_position"), t.get("tee_position"))
    pin = PIN_LABELS.get(t.get("pin_position"), t.get("pin_position"))
    wind = WIND_LABELS.get(t.get("wind_strength"), t.get("wind_strength"))
    green = GREEN_LABELS.get(t.get("green_speed"), t.get("green_speed"))
    return f"{tee} tees • {pin} pins • {wind} wind • {green} greens"


def tee_time_unix(starts_at: str) -> int | None:
    """Unix timestamp for a tee time's ``starts_at`` ISO datetime.

    Handles a trailing 'Z' and assumes UTC when the value is naive.
    Returns None when the value can't be parsed.
    """
    try:
        s = (starts_at or "").strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except (ValueError, TypeError, AttributeError):
        return None


def tee_time_passed(starts_at: str) -> bool:
    """True when now (UTC) is at or past the tee time start.

    Fails OPEN (returns True) on unparseable values so a data glitch
    can never brick score entry.
    """
    unix = tee_time_unix(starts_at)
    if unix is None:
        return True
    return time.time() >= unix


def score_button_scores(par: int) -> list[int]:
    """Score-button labels for tap-to-enter: par-2 … par+3.

    Clamped to the 1-15 valid score range so the buttons always submit
    legal scores.
    """
    return [s for s in range(par - 2, par + 4) if 1 <= s <= 15]


def parse_hole_scores(text: str, holes: int) -> list[int]:
    """Parse comma-separated hole scores.

    Returns a list of ints of length ``holes``.
    Raises ValueError with a user-friendly message on any problem.
    """
    parts = [p.strip() for p in text.replace(";", ",").split(",")]
    parts = [p for p in parts if p]
    scores: list[int] = []
    for p in parts:
        if not p.isdigit():
            raise ValueError(
                f"'{p}' is not a whole number. Enter {holes} hole scores "
                f"separated by commas, e.g. {', '.join(['4'] * min(holes, 9))}."
            )
        v = int(p)
        if not 1 <= v <= 15:
            raise ValueError(f"Score {v} is out of range — holes are scored 1-15.")
        scores.append(v)
    if len(scores) != holes:
        raise ValueError(
            f"Expected {holes} hole scores but got {len(scores)}. "
            "Double-check you entered one number per hole."
        )
    return scores


def parse_pars(text: str, holes: int) -> list[int]:
    """Parse comma-separated pars; same shape rules as scores, range 3-6."""
    parts = [p.strip() for p in text.replace(";", ",").split(",")]
    parts = [p for p in parts if p]
    pars: list[int] = []
    for p in parts:
        if not p.isdigit():
            raise ValueError(f"'{p}' is not a whole number.")
        v = int(p)
        if not 3 <= v <= 6:
            raise ValueError(f"Par {v} is out of range — pars are 3-6.")
        pars.append(v)
    if len(pars) != holes:
        raise ValueError(f"Expected {holes} pars but got {len(pars)}.")
    return pars


def total(scores: Iterable[int]) -> int:
    return sum(scores)


def to_par(score_total: int, pars: Optional[Iterable[int]]) -> Optional[int]:
    """Strokes relative to par, or None when pars are unknown."""
    if not pars:
        return None
    return score_total - sum(pars)


def format_to_par(tp: Optional[int]) -> str:
    """'+3' / 'E' / '-2' style formatting ('' when unknown)."""
    if tp is None:
        return ""
    if tp == 0:
        return "E"
    return f"{tp:+d}"


def best_ball_holes(cards: list[list[int]]) -> list[int]:
    """Per-hole best (minimum) across team member cards.

    All cards must cover the same number of holes.
    Raises ValueError on empty input or mismatched lengths.
    """
    if not cards:
        raise ValueError("No scorecards to aggregate.")
    n = len(cards[0])
    for c in cards:
        if len(c) != n:
            raise ValueError("Scorecards cover different numbers of holes.")
    return [min(hole_scores) for hole_scores in zip(*cards)]


def best_ball_total(cards: list[list[int]]) -> int:
    return total(best_ball_holes(cards))


def match_records(matches: Iterable[dict]) -> dict[str, dict[str, int]]:
    """Build W-L-T records from confirmed matches.

    Each match dict needs: player1, player2, winner in
    {'player1', 'player2', 'tie'}.
    Returns {player_id: {'w': int, 'l': int, 't': int}}.
    """
    records: dict[str, dict[str, int]] = {}

    def ensure(pid: str) -> dict[str, int]:
        return records.setdefault(pid, {"w": 0, "l": 0, "t": 0})

    for m in matches:
        p1, p2, winner = m["player1"], m["player2"], m.get("winner")
        if winner == "tie":
            ensure(p1)["t"] += 1
            ensure(p2)["t"] += 1
        elif winner == "player1":
            ensure(p1)["w"] += 1
            ensure(p2)["l"] += 1
        elif winner == "player2":
            ensure(p2)["w"] += 1
            ensure(p1)["l"] += 1
        # unknown/None winner: not a decided match, skip
    return records


def rank_stroke(entries: list[dict]) -> list[dict]:
    """Sort stroke-play entries ascending by total. Entries need 'total'."""
    return sorted(entries, key=lambda e: e["total"])


def rank_scramble(entries: list[dict]) -> list[dict]:
    """Sort scramble team entries ascending by total.

    Each team submits one combined scorecard, so ranking is by team total
    (like stroke play); ties break alphabetically by team name for a stable
    order. Entries need 'total' and may have 'name'.
    """
    return sorted(entries, key=lambda e: (e["total"], str(e.get("name", ""))))


def rank_match_records(records: dict[str, dict[str, int]]) -> list[tuple[str, dict[str, int]]]:
    """Sort match-play records: most wins, then fewest losses, then most ties."""
    return sorted(
        records.items(),
        key=lambda kv: (-kv[1]["w"], kv[1]["l"], -kv[1]["t"]),
    )


# ------------------------------------------------- season points & career stats
_POINTS_TABLE = {1: 100, 2: 90, 3: 80, 4: 70, 5: 60,
                 6: 50, 7: 40, 8: 30, 9: 20, 10: 10}


def points_for_position(pos: int) -> int:
    """Season points for a 1-indexed finishing position.

    Top 10 scale down from 100; everyone else earns 5 participation points.
    """
    try:
        pos = int(pos)
    except (TypeError, ValueError):
        pos = 1
    if pos < 1:
        pos = 1
    return _POINTS_TABLE.get(pos, 5)


def career_stats(rounds: list[dict]) -> dict:
    """Aggregate career stats from verified rounds.

    Each round is ``{"holes": [scores...], "pars": [pars...] | None}``.
    Scoring categories (birdies etc.) and the par streak only count rounds
    with known pars; rounds without pars still count toward totals.
    """
    totals = [sum(r["holes"]) for r in rounds if r.get("holes")]
    stats = {
        "rounds_played": len(rounds),
        "best_total": min(totals) if totals else None,
        "best_to_par": None,
        "avg_total": (sum(totals) / len(totals)) if totals else None,
        "birdies_or_better": 0,
        "pars_made": 0,
        "bogeys": 0,
        "doubles_or_worse": 0,
        "best_par_streak": None,
    }
    best_tp: Optional[int] = None
    best_streak = 0
    pars_known = False
    for r in rounds:
        holes = r.get("holes") or []
        pars = r.get("pars")
        if not pars or len(pars) != len(holes):
            continue
        pars_known = True
        tp = sum(holes) - sum(pars)
        if best_tp is None or tp < best_tp:
            best_tp = tp
        streak = 0
        for score, par in zip(holes, pars):
            diff = score - par
            if diff <= -1:
                stats["birdies_or_better"] += 1
            elif diff == 0:
                stats["pars_made"] += 1
            elif diff == 1:
                stats["bogeys"] += 1
            else:  # diff >= 2
                stats["doubles_or_worse"] += 1
            if score <= par:
                streak += 1
                if streak > best_streak:
                    best_streak = streak
            else:
                streak = 0
    stats["best_to_par"] = best_tp
    stats["best_par_streak"] = best_streak if pars_known else None
    return stats


# ------------------------------------------------- date helpers (scheduling)
def parse_date(text: str):
    """Parse a YYYY-MM-DD string into a ``datetime.date``.

    Raises ValueError with a user-friendly message when the text isn't a
    real calendar date.
    """
    try:
        return datetime.strptime((text or "").strip(), "%Y-%m-%d").date()
    except (ValueError, AttributeError, TypeError):
        raise ValueError(
            f"'{text}' isn't a real calendar date — use YYYY-MM-DD, "
            "e.g. 2026-10-03."
        )


def validate_date_range(start_s: str, end_s: str):
    """Validate a tournament date range.

    Returns ``(start_date, end_date)`` as ``datetime.date`` objects.
    Raises ValueError when either date is invalid or the end predates
    the start.
    """
    start = parse_date(start_s)
    end = parse_date(end_s)
    if end < start:
        raise ValueError("The end date can't be before the start date.")
    return start, end


_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def format_date(iso_or_none: str | None) -> str:
    """'2026-10-03' -> 'Oct 3'. Returns 'TBD' for missing/invalid input."""
    if not iso_or_none:
        return "TBD"
    try:
        d = parse_date(iso_or_none)
    except ValueError:
        return "TBD"
    return f"{_MONTHS[d.month - 1]} {d.day}"


def format_date_range(start_iso: str | None, end_iso: str | None) -> str:
    """'2026-10-03','2026-10-10' -> 'Oct 3 – Oct 10'.

    A single-day range renders as just 'Oct 3'; missing dates render 'TBD'.
    """
    if not start_iso or not end_iso:
        return "TBD"
    try:
        start, end = validate_date_range(start_iso, end_iso)
    except ValueError:
        return "TBD"
    start_s = f"{_MONTHS[start.month - 1]} {start.day}"
    if start == end:
        return start_s
    return f"{start_s} – {_MONTHS[end.month - 1]} {end.day}"


# ------------------------------------------------------- player-list parsing
def parse_names_list(text: str, max_names: int = 8) -> list[str]:
    """Split a buddies/players input into display names.

    Accepts comma-, semicolon-, or newline-separated names (display names may
    contain spaces, so bare spaces are NOT treated as separators). Discord
    mentions like ``<@123>`` / ``<@!123>`` are kept as-is for later
    resolution. Raises ValueError when no names are found or the cap is
    exceeded.
    """
    parts = re.split(r"[,\n;]+", text or "")
    names = [p.strip() for p in parts if p.strip()]
    if not names:
        raise ValueError("List at least one player, e.g. `Alice, Bob`.")
    if len(names) > max_names:
        raise ValueError(f"Too many players — max {max_names} per round.")
    return names


def split_score_cards(text: str) -> list[str]:
    """Split a scores input into individual scorecard strings on ';'.

    Used by side-quest logging: one card per player for stroke play (in
    player order), a single team card for alt_shot/scramble.
    """
    cards = [(c or "").strip() for c in (text or "").split(";")]
    cards = [c for c in cards if c]
    if not cards:
        raise ValueError("Enter at least one scorecard of hole scores.")
    return cards

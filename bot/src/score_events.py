"""Detect notable scoring events after a scorecard write and queue pushes.

Called after every scorecard save (API live saves + final submits, and the
Discord tap UI). Compares the card's holes before/after the write:

- ace: a hole newly scored 1 (hole-in-one)
- albatross: a hole newly at 3-under par or better (excluding aces)
- top3: the stroke leaderboard's top-3 order changed (live cards count)

Pushes go to registered tournament players whose notification prefs
allow that event kind; the per-minute APNs drain in the API sends them.
"""

import json

from . import db
from . import leaderboard_render as lr


def _holes_of(card: dict | None, n: int) -> list:
    if not card:
        return [None] * n
    try:
        holes = json.loads(card.get("holes_json") or "[]")
    except (ValueError, TypeError):
        holes = []
    holes = list(holes) + [None] * n
    return holes[:n]


def _pars_of(tournament: dict) -> list[int]:
    out = []
    for bit in (tournament.get("pars") or "").split(","):
        try:
            out.append(int(bit))
        except (ValueError, TypeError):
            pass
    return out


async def detect_score_events(
    db_path: str,
    tournament_id,
    player_discord_id: str,
    old_card: dict | None,
    new_card: dict,
) -> list[str]:
    """Compare old/new cards; enqueue pushes for newsworthy events.

    Returns the list of event kinds fired ('ace', 'albatross', 'top3').
    Never raises — push must not break scoring.
    """
    fired: list[str] = []
    try:
        t = await db.get_tournament(db_path, tournament_id)
        if t is None:
            return fired
        n = t.get("holes") or 18
        pars = _pars_of(t)
        old_holes = _holes_of(old_card, n)
        new_holes = _holes_of(new_card, n)

        player = await db.get_player(db_path, player_discord_id)
        name = db.display_name_of(player, player_discord_id)
        course = t.get("course") or "the course"

        for i, (old, new) in enumerate(zip(old_holes, new_holes)):
            if new is None:
                continue
            hole_no = i + 1
            par = pars[i] if i < len(pars) else None
            was_ace = old == 1
            is_ace = new == 1
            was_alba = (
                old is not None and old != 1 and par and old <= par - 3
            )
            is_alba = (
                new != 1 and par and new <= par - 3
            )
            if is_ace and not was_ace:
                await db.notify_tournament_players(
                    db_path, tournament_id, "ace",
                    title="🏌️ Hole-in-one!",
                    body=f"{name} aced hole {hole_no} at {course}!",
                    data={"type": "ace", "tournament_id": tournament_id,
                          "player_discord_id": player_discord_id,
                          "hole": hole_no},
                )
                await db.enqueue_outbox(
                    db_path, "score_highlight",
                    {"tournament_id": tournament_id, "event_type": "ace",
                     "player_discord_id": player_discord_id,
                     "player_name": name, "hole": hole_no,
                     "course": course})
                fired.append("ace")
            elif is_alba and not was_alba:
                await db.notify_tournament_players(
                    db_path, tournament_id, "albatross",
                    title="🦅 Albatross!",
                    body=f"{name} went {par - new}-under on hole {hole_no}"
                         f" at {course}!",
                    data={"type": "albatross",
                          "tournament_id": tournament_id,
                          "player_discord_id": player_discord_id,
                          "hole": hole_no},
                )
                await db.enqueue_outbox(
                    db_path, "score_highlight",
                    {"tournament_id": tournament_id,
                     "event_type": "albatross",
                     "player_discord_id": player_discord_id,
                     "player_name": name, "hole": hole_no,
                     "course": course,
                     "under": par - new if par and new else None})
                fired.append("albatross")

        # Top-3 movement (stroke leaderboards; live cards count).
        if (t.get("format") or "stroke") == "stroke":
            ranked, _ = await lr._stroke_ranked(
                db_path, t, include_in_progress=True)
            top3 = [r["player_discord_id"] for r in ranked[:3]]
            prev = await db.get_leaderboard_snapshot(db_path, tournament_id)
            await db.set_leaderboard_snapshot(db_path, tournament_id, top3)
            if prev and prev != top3 and len(top3) >= 2:
                names = []
                for pid in top3:
                    p = await db.get_player(db_path, pid)
                    names.append(db.display_name_of(p, pid))
                await db.notify_tournament_players(
                    db_path, tournament_id, "top3_changes",
                    title="📊 Top 3 shake-up",
                    body=" → ".join(names[:3]),
                    data={"type": "top3", "tournament_id": tournament_id,
                          "top3": top3},
                )
                await db.enqueue_outbox(
                    db_path, "score_highlight",
                    {"tournament_id": tournament_id, "event_type": "top3",
                     "top3_names": names[:3], "course": course})
                fired.append("top3")
    except Exception:
        import logging
        logging.getLogger("vgc.push").exception(
            "score event detection failed")
    return fired

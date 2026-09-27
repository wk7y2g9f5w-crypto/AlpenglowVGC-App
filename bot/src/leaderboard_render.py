"""Live leaderboard rendering.

``build_leaderboard_embed`` is the single source of truth used by both the
``/leaderboard`` command and the auto-refresh that edits the pinned board
message after every scoring event.
"""
import json
from datetime import datetime, timezone

import discord

from src import db
from src import scoring_logic as sl


def _pars_list(tournament: dict) -> list[int] | None:
    if tournament.get("pars"):
        try:
            return [int(x) for x in tournament["pars"].split(",")]
        except ValueError:
            return None
    return None


def _format_label(fmt: str) -> str:
    return {
        "stroke": "Stroke play",
        "match": "Match play",
        "best_ball": "Best ball",
        "alt_shot": "Alternate shot",
        "scramble": "Scramble",
    }.get(fmt, fmt)


def _total_suffix(total: int, pars: list[int] | None) -> str:
    tp = sl.format_to_par(sl.to_par(total, pars))
    return f" ({tp})" if tp else ""


def _latest_by_player(cards: list[dict]) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for c in sorted(cards, key=lambda c: c["submitted_at"]):
        if c["player_discord_id"]:
            latest[c["player_discord_id"]] = c
    return latest


async def build_leaderboard_embed(db_path: str, tournament_id: int,
                                  final: bool = False) -> discord.Embed | None:
    """Build the leaderboard embed for a tournament. Returns None if not found."""
    t = await db.get_tournament(db_path, tournament_id)
    if t is None:
        return None
    fmt = t["format"]
    pars = _pars_list(t)
    title = f"🏆 {t['name']} — {'Final Standings' if final else 'Live Leaderboard'}"
    embed = discord.Embed(
        title=title,
        description=f"{_format_label(fmt)} • {t['holes']} holes • {t['course']}\n"
                    f"⛳ {sl.format_settings(t)}",
        color=0x2E7D32 if final else 0x1B6CA8,
    )
    if fmt == "stroke":
        await _render_stroke(embed, t, pars, db_path)
    elif fmt in ("best_ball", "alt_shot", "scramble"):
        await _render_teams(embed, t, pars, db_path)
    elif fmt == "match":
        await _render_match(embed, t, db_path)

    now = int(datetime.now(timezone.utc).timestamp())
    # Discord renders <t:..:R> relative timestamps in footers.
    embed.set_footer(text=f"Updated <t:{now}:R>")
    return embed


async def _stroke_ranked(db_path: str, t: dict
                      ) -> tuple[list[dict], list[dict]]:
    """Latest cards per player: (verified ranked by total, pending by total)."""
    cards = await db.get_scorecards(db_path, t["id"])
    latest = _latest_by_player(cards)
    verified = [c for c in latest.values() if c["status"] == "verified"]
    pending = [c for c in latest.values() if c["status"] == "pending"]
    return sl.rank_stroke(verified), sorted(pending, key=lambda c: c["total"])


async def _render_stroke(embed: discord.Embed, t: dict,
                         pars: list[int] | None, db_path: str) -> None:
    ranked, pending = await _stroke_ranked(db_path, t)

    if not ranked and not pending:
        embed.add_field(name="No scores yet",
                        value="Scores will appear here once players submit them.",
                        inline=False)
        return

    lines = []
    for i, c in enumerate(ranked, start=1):
        player = await db.get_player(db_path, c["player_discord_id"])
        name = db.display_name_of(player, c["player_discord_id"])
        lines.append(f"**{i}.** {name} — **{c['total']}**{_total_suffix(c['total'], pars)}")
    if lines:
        embed.add_field(name="Standings", value="\n".join(lines[:25]), inline=False)
    if pending:
        plines = []
        for c in pending:
            player = await db.get_player(db_path, c["player_discord_id"])
            name = db.display_name_of(player, c["player_discord_id"])
            plines.append(f"• ⏳ {name} — **{c['total']}**{_total_suffix(c['total'], pars)}")
        embed.add_field(
            name="Awaiting verification (not ranked)",
            value="\n".join(plines[:10]),
            inline=False,
        )


async def _team_rows(db_path: str, t: dict) -> tuple[list[dict], list[str]]:
    """Ranked team rows (each includes team_id) plus scoreless team names."""
    teams = await db.get_teams(db_path, t["id"])
    rows: list[dict] = []
    scoreless: list[str] = []
    for team in teams:
        members = await db.get_team_members(db_path, team["id"])
        member_cards: list[list[int]] = []
        has_pending = False
        if t["format"] in ("alt_shot", "scramble"):
            # One shared team card (submitted under the team name).
            card = await db.get_latest_team_card(db_path, t["id"], team["id"])
            cards = [card] if card else []
        else:
            cards = [await db.get_latest_player_card(db_path, t["id"], m["discord_id"])
                     for m in members]
        for card in cards:
            if card is None:
                continue
            if card["status"] != "verified":
                has_pending = True
                continue
            try:
                member_cards.append(json.loads(card["holes_json"]))
            except (ValueError, TypeError):
                continue
        if not member_cards:
            scoreless.append(team["name"])
            continue
        try:
            if t["format"] == "best_ball":
                team_total = sl.best_ball_total(member_cards)
            else:  # alt_shot / scramble: one shared team card
                team_total = sum(member_cards[0])
        except ValueError:
            scoreless.append(f"{team['name']} (card mismatch)")
            continue
        rows.append({"team_id": team["id"], "name": team["name"],
                     "total": team_total, "members": len(members),
                     "pending": has_pending})

    if t["format"] == "scramble":
        rows = sl.rank_scramble(rows)
    else:
        rows.sort(key=lambda r: r["total"])
    return rows, scoreless


async def _render_teams(embed: discord.Embed, t: dict,
                        pars: list[int] | None, db_path: str) -> None:
    teams = await db.get_teams(db_path, t["id"])
    if not teams:
        embed.add_field(name="No teams yet",
                        value="Create a team with `/team create` first.",
                        inline=False)
        return
    rows, scoreless = await _team_rows(db_path, t)

    if rows:
        lines = []
        for i, r in enumerate(rows, start=1):
            mark = " ⏳" if r["pending"] else ""
            lines.append(
                f"**{i}.** {r['name']} — **{r['total']}**"
                f"{_total_suffix(r['total'], pars)} ({r['members']} players){mark}"
            )
        embed.add_field(name="Team standings", value="\n".join(lines[:25]), inline=False)
    if scoreless:
        embed.add_field(name="No verified scores yet",
                        value=", ".join(scoreless[:10]), inline=False)


async def _render_match(embed: discord.Embed, t: dict, db_path: str) -> None:
    matches = await db.list_matches(db_path, t["id"], status="confirmed")
    records = sl.match_records(matches)
    if not records:
        embed.add_field(name="No confirmed matches yet",
                        value="Report results with `/report_match`.", inline=False)
    else:
        lines = []
        for i, (pid, rec) in enumerate(sl.rank_match_records(records), start=1):
            player = await db.get_player(db_path, pid)
            name = db.display_name_of(player, pid)
            lines.append(f"**{i}.** {name} — **{rec['w']}W {rec['l']}L {rec['t']}T**")
        embed.add_field(name="Standings", value="\n".join(lines[:25]), inline=False)
    pending = await db.list_matches(db_path, t["id"], status="pending")
    if pending:
        plines = []
        for m in pending[:10]:
            p1 = await db.get_player(db_path, m["player1"])
            p2 = await db.get_player(db_path, m["player2"])
            n1 = db.display_name_of(p1, m["player1"])
            n2 = db.display_name_of(p2, m["player2"])
            note = f" ({m['score_note']})" if m.get("score_note") else ""
            plines.append(f"• {n1} vs {n2}{note} — awaiting confirmation")
        embed.add_field(name="Pending results", value="\n".join(plines), inline=False)


def _leader_sort_str(fmt: str, sort_value) -> str:
    """Canonical comparable string for a leader.

    Stroke/team formats: zero-padded total (lower is better). Match:
    "w-l-t" with each part zero-padded (compared as (w, -l, t)).
    """
    if fmt == "match":
        w, nl, ties = sort_value
        return f"{w:04d}-{-nl:04d}-{ties:04d}"
    return f"{int(sort_value):05d}"


def _is_better_leader(fmt: str, old_sort: str, new_sort: str) -> bool:
    """True when the new leader's sort value strictly beats the stored one."""
    try:
        if fmt == "match":
            ow, ol, ot = (int(x) for x in old_sort.split("-"))
            nw, nl, nt = (int(x) for x in new_sort.split("-"))
            return (nw, -nl, nt) > (ow, -ol, ot)
        return int(new_sort) < int(old_sort)
    except (ValueError, TypeError, AttributeError):
        return False


async def get_leader(db_path: str, tournament_id: int) -> dict | None:
    """Current leader: {"key", "name", "scoreline", "sort_value"}.

    key = player discord_id (stroke/match) or team name (team formats);
    sort_value is the canonical comparable string from _leader_sort_str.
    Returns None when nothing is ranked yet.
    """
    t = await db.get_tournament(db_path, tournament_id)
    if t is None:
        return None
    pars = _pars_list(t)
    fmt = t["format"]
    if fmt == "stroke":
        ranked, _ = await _stroke_ranked(db_path, t)
        if not ranked:
            return None
        c = ranked[0]
        player = await db.get_player(db_path, c["player_discord_id"])
        return {
            "key": c["player_discord_id"],
            "name": db.display_name_of(player, c["player_discord_id"]),
            "scoreline": f"{c['total']}{_total_suffix(c['total'], pars)}",
            "sort_value": _leader_sort_str(fmt, c["total"]),
        }
    if fmt in ("best_ball", "alt_shot", "scramble"):
        rows, _ = await _team_rows(db_path, t)
        if not rows:
            return None
        r = rows[0]
        return {
            "key": r["name"],
            "name": r["name"],
            "scoreline": f"{r['total']}{_total_suffix(r['total'], pars)}",
            "sort_value": _leader_sort_str(fmt, r["total"]),
        }
    if fmt == "match":
        matches = await db.list_matches(db_path, t["id"], status="confirmed")
        ranked = sl.rank_match_records(sl.match_records(matches))
        if not ranked:
            return None
        pid, rec = ranked[0]
        player = await db.get_player(db_path, pid)
        return {
            "key": pid,
            "name": db.display_name_of(player, pid),
            "scoreline": f"{rec['w']}W {rec['l']}L {rec['t']}T",
            "sort_value": _leader_sort_str(fmt, (rec["w"], -rec["l"], rec["t"])),
        }
    return None


async def final_standings_points(db_path: str, tournament_id: int) -> list[dict]:
    """Per-player season-points rows for a tournament.

    Returns [{"player_discord_id", "position", "points"}]. Ties share the
    position's points (competition ranking — no averaging). Team formats:
    every team member earns the team's position points.
    """
    t = await db.get_tournament(db_path, tournament_id)
    if t is None:
        return []
    fmt = t["format"]
    rows: list[dict] = []

    def award(ranked: list[tuple[int, list[str]]]) -> None:
        """ranked = [(total_or_key, [player_ids...])] in standing order."""
        prev = None
        pos = 0
        for i, (key, pids) in enumerate(ranked):
            if key != prev:
                pos = i + 1
                prev = key
            pts = sl.points_for_position(pos)
            for pid in pids:
                rows.append({"player_discord_id": pid,
                             "position": pos, "points": pts})

    if fmt == "stroke":
        ranked, _ = await _stroke_ranked(db_path, t)
        award([(c["total"], [c["player_discord_id"]]) for c in ranked])
    elif fmt in ("best_ball", "alt_shot", "scramble"):
        team_rows, _ = await _team_rows(db_path, t)
        ranked: list[tuple[int, list[str]]] = []
        for r in team_rows:
            members = await db.get_team_members(db_path, r["team_id"])
            ranked.append((r["total"], [m["discord_id"] for m in members]))
        award(ranked)
    elif fmt == "match":
        matches = await db.list_matches(db_path, t["id"], status="confirmed")
        ranked_records = sl.rank_match_records(sl.match_records(matches))
        award([((rec["w"], rec["l"], rec["t"]), [pid])
               for pid, rec in ranked_records])
    return rows


async def _check_lead_change(bot, db_path: str, t: dict, channel) -> None:
    """Post a lead-change notice when the leader is overtaken. Never raises."""
    try:
        leader = await get_leader(db_path, t["id"])
        if leader is None:
            return
        stored = await db.get_leader(db_path, t["id"])
        if stored is None:
            # First ranked board — establish the baseline silently.
            await db.set_leader(db_path, t["id"],
                                leader["key"], leader["sort_value"])
            return
        if stored["leader_key"] == leader["key"]:
            # Same leader; keep the stored sort value current.
            if stored["leader_sort"] != leader["sort_value"]:
                await db.set_leader(db_path, t["id"],
                                    leader["key"], leader["sort_value"])
            return
        # Different leader: update the baseline, notify only on strict improvement.
        await db.set_leader(db_path, t["id"],
                            leader["key"], leader["sort_value"])
        if t.get("status") == "completed":
            return
        if _is_better_leader(t["format"], stored["leader_sort"],
                             leader["sort_value"]):
            try:
                await channel.send(
                    f"🔄 **Lead change!** {leader['name']} takes the lead in "
                    f"**{t['name']}** ({leader['scoreline']})."
                )
            except Exception:
                pass  # notification is best-effort
    except Exception:
        pass  # lead tracking must never break a refresh


async def refresh_leaderboard(bot, db_path: str, tournament_id: int) -> bool:
    """Re-render and EDIT the pinned leaderboard message. Never raises."""
    try:
        t = await db.get_tournament(db_path, tournament_id)
        if not t or not t.get("leaderboard_channel_id") or not t.get("leaderboard_message_id"):
            return False
        channel = bot.get_channel(int(t["leaderboard_channel_id"]))
        if channel is None:
            try:
                channel = await bot.fetch_channel(int(t["leaderboard_channel_id"]))
            except Exception:
                return False
        try:
            message = await channel.fetch_message(int(t["leaderboard_message_id"]))
        except Exception:
            return False
        embed = await build_leaderboard_embed(db_path, tournament_id)
        if embed is None:
            return False
        await message.edit(embed=embed)
        await _check_lead_change(bot, db_path, t, channel)
        return True
    except Exception:
        return False

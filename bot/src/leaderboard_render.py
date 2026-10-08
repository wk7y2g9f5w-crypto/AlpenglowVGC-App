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


def _round_settings_line(r: dict) -> str:
    """'Middle tees • White pins • Moderate wind' for a rounds row."""
    tee = sl.TEE_LABELS.get(r.get("tee_position"), r.get("tee_position"))
    pin = sl.PIN_LABELS.get(r.get("pin_position"), r.get("pin_position"))
    wind = sl.WIND_LABELS.get(r.get("wind_strength"), r.get("wind_strength"))
    return f"{tee} tees • {pin} pins • {wind} wind"


async def _rounds_block(db_path: str, t: dict) -> tuple[int, str]:
    """(num_rounds, settings text) for embeds/announcements."""
    rounds = await db.list_rounds(db_path, t["id"])
    n = len(rounds)
    if n <= 1:
        return n, ""
    lines = [f"🔁 {n} rounds × {t['holes']} holes"]
    for r in rounds:
        lines.append(f"R{r['round_number']}: {_round_settings_line(r)}")
    return n, "\n".join(lines)


async def build_leaderboard_embed(db_path: str, tournament_id: int,
                                  final: bool = False) -> discord.Embed | None:
    """Build the leaderboard embed for a tournament. Returns None if not found."""
    t = await db.get_tournament(db_path, tournament_id)
    if t is None:
        return None
    fmt = t["format"]
    pars = _pars_list(t)
    num_rounds, rounds_text = await _rounds_block(db_path, t)
    if num_rounds > 1:
        header = f"{_format_label(fmt)} • {t['course']}"
        settings_text = rounds_text
    else:
        header = f"{_format_label(fmt)} • {t['holes']} holes • {t['course']}"
        settings_text = f"⛳ {sl.format_settings(t)}"
    title = f"🏆 {t['name']} — {'Final Standings' if final else 'Live Leaderboard'}"
    embed = discord.Embed(
        title=title,
        description=f"{header}\n{settings_text}",
        color=0x2E7D32 if final else 0x1B6CA8,
    )
    if fmt == "stroke":
        await _render_stroke(embed, t, pars, db_path,
                             include_in_progress=not final)
    elif fmt in ("best_ball", "alt_shot", "scramble"):
        await _render_teams(embed, t, pars, db_path,
                            include_in_progress=not final)
    elif fmt == "match":
        await _render_match(embed, t, db_path)

    now = int(datetime.now(timezone.utc).timestamp())
    # Discord renders <t:..:R> relative timestamps in footers.
    embed.set_footer(text=f"Updated <t:{now}:R>")
    return embed


def _holes_played(card: dict) -> int:
    """How many holes have an entered score on this card."""
    try:
        return sum(1 for s in json.loads(card["holes_json"]) if s is not None)
    except (ValueError, TypeError):
        return 0


def _partial_round_score(scores: list, pars: list[int] | None
                         ) -> tuple[int, int | None, int]:
    """(total of entered holes, to-par vs pars of entered holes, thru)."""
    thru = sum(1 for s in scores if s is not None)
    total = sum(s for s in scores if s is not None)
    to_par = None
    if pars and len(pars) == len(scores):
        to_par = total - sum(par for par, s in zip(pars, scores)
                             if s is not None)
    return total, to_par, thru


async def _stroke_ranked(db_path: str, t: dict,
                         include_in_progress: bool = False,
                         include_pending: bool = True,
                         ) -> tuple[list[dict], list[dict]]:
    """Multi-round stroke leaderboard: (ranked, pending).

    Ranked rows carry player_discord_id, name, total (verified aggregate),
    to_par (vs par x rounds_played), rounds_played, pending_rounds, and a
    per-round "rounds" breakdown. Players rank by cumulative to-par (total
    when pars are unknown). Players with no verified round appear in
    pending with their latest pending card (historic shape).

    With include_in_progress=True, in-progress (live hole-by-hole) cards
    join the board with partial totals, to-par against the pars of holes
    actually played, and a per-round "thru" count; rows carry on_course=True
    while any counted round is live. With the flag off, behavior is
    exactly as before: live cards stay out of the standings.

    Pending (submitted, awaiting verification) cards keep their leaderboard
    place by default (include_pending=True). Season-points/final standings
    pass include_pending=False so unverified cards don't earn points.
    """
    rounds = await db.list_rounds(db_path, t["id"])
    cards = await db.get_scorecards(db_path, t["id"])
    pars = _pars_list(t)
    par_round = sum(pars) if pars else None

    latest: dict[tuple[str, int], dict] = {}
    for c in sorted(cards, key=lambda c: c["submitted_at"]):
        if c["player_discord_id"] is None:
            continue
        latest[(c["player_discord_id"], c.get("round_number") or 1)] = c

    by_player: dict[str, dict[int, dict]] = {}
    for (pid, rnd), c in latest.items():
        by_player.setdefault(pid, {})[rnd] = c

    ranked, pending = [], []
    for pid, by_round in by_player.items():
        player = await db.get_player(db_path, pid)
        name = db.display_name_of(player, pid)
        verified = {r: c for r, c in by_round.items()
                    if c["status"] == "verified"}
        live: dict[int, dict] = {}
        if include_in_progress:
            live = {r: c for r, c in by_round.items()
                    if (c["status"] == "in_progress" and r not in verified
                        and _holes_played(c) > 0)}
        # Pending (submitted, awaiting verification) cards keep their
        # leaderboard place — they count like verified for ranking so
        # verification itself never causes a false "shake-up".
        # (Disabled for season-points via include_pending=False.)
        awaiting: dict[int, dict] = {}
        if include_pending:
            awaiting = {r: c for r, c in by_round.items()
                        if (c["status"] == "pending" and r not in verified
                            and r not in live)}
        counted = {**verified, **live, **awaiting}
        if not counted:
            pending.append(max(by_round.values(),
                               key=lambda c: c["submitted_at"]))
            continue
        total = 0
        pars_entered = 0
        on_course = bool(live)
        detail, pend_rounds = [], 0
        for r in rounds:
            rn = r["round_number"]
            c = by_round.get(rn)
            if c is not None and c["status"] == "verified":
                detail.append({
                    "round_number": rn, "total": c["total"],
                    "to_par": (c["total"] - par_round
                               if par_round is not None else None),
                    "status": "verified",
                    "thru": t["holes"],
                })
            elif rn in awaiting:
                ac = awaiting[rn]
                detail.append({
                    "round_number": rn, "total": ac["total"],
                    "to_par": (ac["total"] - par_round
                               if par_round is not None else None),
                    "status": "pending",
                    "thru": t["holes"],
                })
            elif rn in live:
                scores = json.loads(c["holes_json"])
                subtotal, sub_to_par, thru = _partial_round_score(scores,
                                                                 pars)
                total += subtotal
                if sub_to_par is not None:
                    pars_entered += subtotal - sub_to_par
                detail.append({
                    "round_number": rn, "total": subtotal,
                    "to_par": sub_to_par, "status": "in_progress",
                    "thru": thru,
                })
            else:
                if c is not None:
                    pend_rounds += 1
                detail.append({
                    "round_number": rn, "total": None, "to_par": None,
                    "status": c["status"] if c else "not_started",
                    "thru": 0,
                })
        for rn, c in verified.items():
            total += c["total"]
            if par_round is not None:
                pars_entered += par_round
        for rn, c in awaiting.items():
            total += c["total"]
            if par_round is not None:
                pars_entered += par_round
        to_par = (total - pars_entered) if par_round is not None else None
        sort_key = to_par if to_par is not None else total
        ranked.append({
            "player_discord_id": pid, "name": name,
            "total": total, "to_par": to_par,
            "rounds_played": len(counted),
            "pending_rounds": pend_rounds,
            "on_course": on_course,
            "rounds": detail,
            "_sort": (sort_key, total, name),
        })
    ranked.sort(key=lambda r: r.pop("_sort"))
    pending.sort(key=lambda c: c["total"])
    return ranked, pending


def _agg_suffix(total: int, pars: list[int] | None,
                rounds_played: int) -> str:
    """'(+6)' style suffix for an aggregate total across N rounds."""
    tp = sl.format_to_par(sl.to_par(total, (pars or []) * rounds_played))
    return f" ({tp})" if tp else ""


async def _render_stroke(embed: discord.Embed, t: dict,
                         pars: list[int] | None, db_path: str,
                         include_in_progress: bool = False) -> None:
    ranked, pending = await _stroke_ranked(db_path, t,
                                           include_in_progress=include_in_progress)

    if not ranked and not pending:
        embed.add_field(name="No scores yet",
                        value="Scores will appear here as players enter them.",
                        inline=False)
        return

    multi = any(len(r["rounds"]) > 1 for r in ranked)
    lines = []
    for i, r in enumerate(ranked, start=1):
        mark = " ⏳" if r["pending_rounds"] else ""
        if r.get("on_course"):
            mark += " ⛳"
        prog = (f" ({r['rounds_played']}/{len(r['rounds'])} rounds)"
                if multi and r["rounds_played"] < len(r["rounds"]) else "")
        tp = sl.format_to_par(r["to_par"])
        suffix = f" ({tp})" if tp else ""
        thru = ""
        if r.get("on_course") and not multi:
            live_rounds = [d for d in r["rounds"]
                           if d["status"] == "in_progress"]
            if live_rounds:
                thru = f" (thru {live_rounds[0]['thru']})"
        lines.append(f"**{i}.** {r['name']} — **{r['total']}**"
                     f"{suffix}{thru}"
                     f"{prog}{mark}")
        if multi:
            bits = []
            for d in r["rounds"]:
                if d["status"] == "verified":
                    bits.append(f"R{d['round_number']} {d['total']}"
                                f"{_total_suffix(d['total'], pars)}")
                elif d["status"] == "in_progress":
                    bits.append(f"R{d['round_number']} {d['total']} "
                                f"(thru {d['thru']}) ⛳")
                elif d["status"] == "pending":
                    bits.append(f"R{d['round_number']} ⏳")
            if bits:
                lines.append("  ↳ " + " · ".join(bits))
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


def _best_ball_live(all_scores: list[list[int | None]], pars: list[int] | None
                   ) -> tuple[int, int | None, int]:
    """Per-hole best across member cards using entered holes only.

    Returns (total, to-par vs pars of holes with an entry, thru). Raises
    ValueError on card-length mismatch (caller treats as a card mismatch).
    """
    if not all_scores:
        raise ValueError("no cards")
    n = len(all_scores[0])
    if any(len(s) != n for s in all_scores):
        raise ValueError("card length mismatch")
    par_ok = bool(pars) and len(pars) == n
    bests: list[int] = []
    pars_entered = 0
    for i in range(n):
        entered = [s[i] for s in all_scores if s[i] is not None]
        if not entered:
            continue
        bests.append(min(entered))
        if par_ok:
            pars_entered += pars[i]
    total = sum(bests)
    return total, (total - pars_entered) if par_ok else None, len(bests)


async def _team_rows(db_path: str, t: dict,
                     include_in_progress: bool = False
                     ) -> tuple[list[dict], list[str]]:
    """Ranked team rows (each includes team_id) plus scoreless team names.

    Round-aware: best_ball takes the per-hole best within each round, then
    sums round totals; alt_shot/scramble sum each round's shared team card.
    Rows carry total (aggregate), to_par, rounds_played, pending, and a
    per-round "rounds" breakdown.

    With include_in_progress=True, in-progress (live hole-by-hole) cards
    join the board with partial totals and a "thru" count; rows carry
    on_course=True while any counted round is live. With the flag off,
    behavior is exactly as before.
    """
    teams = await db.get_teams(db_path, t["id"])
    rounds = await db.list_rounds(db_path, t["id"])
    pars = _pars_list(t)
    par_round = sum(pars) if pars else None
    rows: list[dict] = []
    scoreless: list[str] = []
    for team in teams:
        members = await db.get_team_members(db_path, team["id"])
        round_totals: dict[int, int] = {}
        has_pending = False
        on_course = False
        mismatch = False
        detail: list[dict] = []
        pars_entered = 0
        for r in rounds:
            rn = r["round_number"]
            round_pending = False
            if t["format"] in ("alt_shot", "scramble"):
                # One shared team card per round (submitted under the team name).
                cards = [await db.get_latest_team_card(db_path, t["id"],
                                                       team["id"], rn)]
            else:
                cards = [await db.get_latest_player_card(
                    db_path, t["id"], m["discord_id"], rn) for m in members]
            verified_scores: list[list[int]] = []
            live_scores: list[list[int | None]] = []
            for card in cards:
                if card is None:
                    continue
                if card["status"] == "verified":
                    try:
                        verified_scores.append(json.loads(card["holes_json"]))
                    except (ValueError, TypeError):
                        continue
                elif (card["status"] == "in_progress"
                      and include_in_progress):
                    has_pending = True
                    round_pending = True
                    try:
                        scores = json.loads(card["holes_json"])
                    except (ValueError, TypeError):
                        continue
                    if any(s is not None for s in scores):
                        live_scores.append(scores)
                else:
                    has_pending = True
                    round_pending = True
            counted_live = bool(live_scores)
            if not verified_scores and not live_scores:
                detail.append({"round_number": rn, "total": None,
                               "to_par": None, "thru": 0,
                               "status": "pending" if round_pending
                               else "not_started"})
                continue
            try:
                if t["format"] == "best_ball":
                    rt, rt_par, thru = _best_ball_live(
                        verified_scores + live_scores, pars)
                else:  # alt_shot / scramble: one shared team card
                    scores = (verified_scores[0] if verified_scores
                              else live_scores[0])
                    rt, rt_par, thru = _partial_round_score(scores, pars)
            except ValueError:
                mismatch = True
                break
            if counted_live:
                on_course = True
            round_totals[rn] = rt
            if rt_par is not None:
                pars_entered += rt - rt_par
            detail.append({
                "round_number": rn, "total": rt, "to_par": rt_par,
                "thru": thru,
                "status": "in_progress" if counted_live else "verified",
            })
        if mismatch:
            scoreless.append(f"{team['name']} (card mismatch)")
            continue
        if not round_totals:
            scoreless.append(team["name"])
            continue
        total = sum(round_totals.values())
        to_par = (total - pars_entered
                  if par_round is not None else None)
        sort_key = to_par if to_par is not None else total
        rows.append({"team_id": team["id"], "name": team["name"],
                     "total": total, "to_par": to_par,
                     "members": len(members), "pending": has_pending,
                     "on_course": on_course,
                     "rounds_played": len(round_totals),
                     "rounds": detail, "_sort": (sort_key, total,
                                                 team["name"])})
    for r in rows:
        r.pop("_sort", None)
    rows.sort(key=lambda r: (r["to_par"] if r["to_par"] is not None
                             else r["total"], r["total"], r["name"]))
    return rows, scoreless


async def _render_teams(embed: discord.Embed, t: dict,
                        pars: list[int] | None, db_path: str,
                        include_in_progress: bool = False) -> None:
    teams = await db.get_teams(db_path, t["id"])
    if not teams:
        embed.add_field(name="No teams yet",
                        value="Create a team with `/team create` first.",
                        inline=False)
        return
    rows, scoreless = await _team_rows(db_path, t,
                                       include_in_progress=include_in_progress)

    multi = any(len(r["rounds"]) > 1 for r in rows)
    if rows:
        lines = []
        for i, r in enumerate(rows, start=1):
            mark = " ⏳" if r["pending"] else ""
            if r.get("on_course"):
                mark += " ⛳"
            prog = (f" ({r['rounds_played']}/{len(r['rounds'])} rounds)"
                    if multi and r["rounds_played"] < len(r["rounds"]) else "")
            thru = ""
            if r.get("on_course") and not multi:
                live_rounds = [d for d in r["rounds"]
                               if d["status"] == "in_progress"]
                if live_rounds:
                    thru = f" (thru {live_rounds[0]['thru']})"
            tp = sl.format_to_par(r["to_par"])
            suffix = f" ({tp})" if tp else ""
            lines.append(
                f"**{i}.** {r['name']} — **{r['total']}**"
                f"{suffix}"
                f" ({r['members']} players){thru}{prog}{mark}"
            )
            if multi:
                bits = []
                for d in r["rounds"]:
                    if d["status"] == "verified":
                        bits.append(f"R{d['round_number']} {d['total']}"
                                    f"{_total_suffix(d['total'], pars)}")
                    elif d["status"] == "in_progress":
                        bits.append(f"R{d['round_number']} {d['total']} "
                                    f"(thru {d['thru']}) ⛳")
                    elif d["status"] == "pending":
                        bits.append(f"R{d['round_number']} ⏳")
                if bits:
                    lines.append("  ↳ " + " · ".join(bits))
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
        return {
            "key": c["player_discord_id"],
            "name": c["name"],
            "scoreline": f"{c['total']}"
                         f"{_agg_suffix(c['total'], pars, c['rounds_played'])}",
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
            "scoreline": f"{r['total']}"
                         f"{_agg_suffix(r['total'], pars, r['rounds_played'])}",
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

    def _rank_key(r: dict):
        """Ranking key shared with display order (to-par when known)."""
        return r["to_par"] if r.get("to_par") is not None else r["total"]

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
        ranked, _ = await _stroke_ranked(db_path, t, include_pending=False)
        award([(_rank_key(c), [c["player_discord_id"]]) for c in ranked])
    elif fmt in ("best_ball", "alt_shot", "scramble"):
        team_rows, _ = await _team_rows(db_path, t)
        ranked: list[tuple[int, list[str]]] = []
        for r in team_rows:
            members = await db.get_team_members(db_path, r["team_id"])
            ranked.append((_rank_key(r), [m["discord_id"] for m in members]))
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

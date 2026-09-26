"""Alpenglow VGC mobile companion API.

Read-only+write JSON bridge over the Discord tournament bot's SQLite
database. Reuses the bot's own modules (``src.db``, ``src.scoring_logic``,
``src.leaderboard_render``, and pure helpers from ``src.cogs``) so the app
and Discord always agree.

This service NEVER modifies the bot's behavior: it shares the one SQLite
file, keeps transactions short (the bot's db layer opens a fresh connection
per call), and never touches Discord roles/channels/messages (bot-only
side effects are skipped).

Auth: every request except /api/health needs
``Authorization: Bearer <discord_user_oauth_token>``. The token is validated
per-request against Discord's /users/@me endpoint and is never stored or
logged.
"""

import json
import os
import re
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

# --------------------------------------------------------------------------
# Bot integration: load the bot's .env first (GUILD_ID / DB_PATH), then put
# the bot dir on sys.path so we can import its modules directly.
# --------------------------------------------------------------------------
BOT_DIR = Path(
    os.environ.get("BOT_DIR", str(Path.home() / "workspace/golf-tournament-bot"))
)
load_dotenv(BOT_DIR / ".env", override=False)
sys.path.insert(0, str(BOT_DIR))

from src import db  # noqa: E402
from src import config  # noqa: E402
from src import scoring_logic as sl  # noqa: E402
from src import leaderboard_render as lr  # noqa: E402
from src.cogs.teetimes import parse_in_tz  # noqa: E402  (pure helper, no Discord I/O)
from src.cogs.stats import _parse_pars  # noqa: E402  (pure helper, no Discord I/O)

# Module-level settings. Tests override DB_PATH with a temp DB; read these at
# request time (never bind them as function defaults) so overrides take effect.
_db_path_raw = config.DB_PATH
DB_PATH = (
    _db_path_raw
    if os.path.isabs(_db_path_raw)
    else str(BOT_DIR / _db_path_raw)
)
GUILD_ID = str(config.GUILD_ID)

import httpx  # noqa: E402
from fastapi import Depends, FastAPI, HTTPException, Request, status  # noqa: E402
from pydantic import BaseModel, Field, field_validator  # noqa: E402


# --------------------------------------------------------------------------
# Discord token validation
# --------------------------------------------------------------------------
async def fetch_discord_user(token: str) -> dict | None:
    """Validate an OAuth bearer token with Discord.

    Returns ``{"id": ..., "display_name": ...}`` on success, None on any
    failure (bad/expired token, network error, unexpected response). Single
    function so unit tests can monkeypatch it. The token is never stored.
    """
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://discord.com/api/v10/users/@me",
                headers={"Authorization": f"Bearer {token}"},
            )
    except httpx.HTTPError:
        return None
    except (httpx.InvalidURL, ValueError, OSError):
        # httpx.InvalidURL: malformed proxy/URL config in the environment
        # (raised at client construction, outside HTTPError). ValueError /
        # OSError: other transport or parsing failures.
        return None
    if resp.status_code != 200:
        return None
    try:
        data = resp.json()
        discord_id = str(data["id"])
    except (ValueError, KeyError, TypeError):
        return None
    display_name = (data.get("global_name") or data.get("username") or "Player")[:80]
    return {"id": discord_id, "display_name": display_name}


async def get_current_user(request: Request) -> dict:
    """Auth dependency: validate the bearer token, ensure the player row."""
    auth = request.headers.get("Authorization", "")
    token = auth[len("Bearer "):].strip() if auth.startswith("Bearer ") else ""
    info = await fetch_discord_user(token) if token else None
    if not info:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Discord token",
        )
    # Mirror the bot's ensure-player flow.
    await db.upsert_player(DB_PATH, info["id"], info["display_name"])
    player = await db.get_player(DB_PATH, info["id"])
    if player is None:  # pragma: no cover - defensive
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not load player profile",
        )
    return player


CurrentUser = Annotated[dict, Depends(get_current_user)]


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def utc_iso(value: str | None) -> str | None:
    """Normalize a stored ``starts_at`` to ISO-8601 UTC.

    Uses the bot's own tee_time_unix parser; unparseable values pass through
    unchanged (same fail-open philosophy as sl.tee_time_passed).
    """
    if not value:
        return None
    unix = sl.tee_time_unix(value)
    if unix is None:
        return value
    return datetime.fromtimestamp(unix, tz=timezone.utc).isoformat()


async def _tournament_or_404(tournament_id: int) -> dict:
    t = await db.get_tournament(DB_PATH, tournament_id)
    if not t or t["guild_id"] != GUILD_ID:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tournament not found",
        )
    return t


async def _tee_time_or_404(tee_time_id: int) -> tuple[dict, dict]:
    """Returns (tee_time, tournament) for a tee time in this guild."""
    tt = await db.get_tee_time(DB_PATH, tee_time_id)
    if not tt:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tee time not found",
        )
    t = await _tournament_or_404(tt["tournament_id"])
    return tt, t


def _player_json(p: dict) -> dict:
    return {
        "discord_id": p.get("discord_id"),
        "display_name": p.get("display_name"),
        "golfplus_handle": p.get("golfplus_handle"),
    }


def _tee_time_json(tt: dict, players: list[dict]) -> dict:
    return {
        "id": tt["id"],
        "label": tt["label"],
        "starts_at": utc_iso(tt.get("starts_at")),
        "max_players": tt["max_players"],
        "created_by": tt["created_by"],
        "players": [_player_json(p) for p in players],
    }


def _card_json(card: dict, pars_csv: str | None) -> dict:
    scores = json.loads(card["holes_json"])
    pars = _parse_pars(pars_csv)
    return {
        "player_discord_id": card["player_discord_id"],
        "scores": scores,
        "total": card["total"],
        "to_par": sl.to_par(card["total"], pars),
        "status": card["status"],
        "submitted_by": card.get("submitted_by"),
    }


def _tournament_json(t: dict, registered: bool) -> dict:
    return {
        "id": t["id"],
        "name": t["name"],
        "format": t["format"],
        "holes": t["holes"],
        "course": t["course"],
        "status": t["status"],
        "start_date": t.get("start_date"),
        "end_date": t.get("end_date"),
        "tee_position": t.get("tee_position"),
        "pin_position": t.get("pin_position"),
        "wind_strength": t.get("wind_strength"),
        "green_speed": t.get("green_speed"),
        "registered": registered,
    }


# --------------------------------------------------------------------------
# Request schemas
# --------------------------------------------------------------------------
class TeeTimeCreate(BaseModel):
    label: str
    date: str  # YYYY-MM-DD
    time: str  # HH:MM 24h
    max_players: int = Field(default=4, ge=1, le=4)

    @field_validator("label")
    @classmethod
    def _label_nonempty(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("label must be non-empty")
        return v[:80]

    @field_validator("date")
    @classmethod
    def _valid_date(cls, v: str) -> str:
        # Same semantics as the bot: sl.parse_date raises ValueError on bad input.
        sl.parse_date(v)
        return v.strip()

    @field_validator("time")
    @classmethod
    def _valid_time(cls, v: str) -> str:
        if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", (v or "").strip()):
            raise ValueError("time must look like `19:30` (24-hour HH:MM)")
        return v.strip()


class ScorecardSubmit(BaseModel):
    player_discord_id: str
    scores: list[int]

    @field_validator("scores")
    @classmethod
    def _scores_in_range(cls, v: list[int]) -> list[int]:
        for s in v:
            if not isinstance(s, int) or isinstance(s, bool) or not 1 <= s <= 15:
                raise ValueError(f"Score {s} is out of range — holes are scored 1-15.")
        return v


class PlayerUpdate(BaseModel):
    timezone: str | None = None
    golfplus_handle: str | None = None


# --------------------------------------------------------------------------
# App
# --------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    import aiosqlite

    if not os.path.exists(DB_PATH):
        raise RuntimeError(f"Bot database not found at {DB_PATH}")
    async with aiosqlite.connect(DB_PATH) as con:
        async with con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ) as cur:
            rows = await cur.fetchall()
    tables = {r[0] for r in rows}
    if "players" not in tables:
        raise RuntimeError(f"{DB_PATH} is missing the players table")
    yield


app = FastAPI(title="Alpenglow VGC companion API", version="1.0.0", lifespan=lifespan)


@app.exception_handler(HTTPException)
async def coded_http_exception_handler(request: Request, exc: HTTPException):
    """Surface machine-readable error codes at the top level.

    The mobile-app contract expects {"code": "..."} — not FastAPI's default
    {"detail": {"code": "..."}} envelope — so the app can match on it.
    Plain-string details keep the standard {"detail": ...} shape.
    """
    from fastapi.responses import JSONResponse

    if isinstance(exc.detail, dict) and "code" in exc.detail:
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(status_code=exc.status_code,
                        content={"detail": exc.detail})


@app.get("/api/health")
async def health() -> dict:
    return {"ok": True}


# --------------------------------------------------------------------------
# Tournaments
# --------------------------------------------------------------------------
@app.get("/api/tournaments")
async def list_tournaments(user: CurrentUser) -> list[dict]:
    rows = await db.list_tournaments(DB_PATH, GUILD_ID)
    out = []
    for t in rows:
        registered = await db.is_registered(DB_PATH, t["id"], user["discord_id"])
        out.append(_tournament_json(t, registered))
    return out


@app.post("/api/tournaments/{tournament_id}/register")
async def register(tournament_id: int, user: CurrentUser) -> dict:
    t = await _tournament_or_404(tournament_id)
    if t["status"] != "registration_open":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "registration_closed"},
        )
    # Mirror the bot's timezone gate exactly (require_timezone).
    if not await db.get_timezone(DB_PATH, user["discord_id"]):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "timezone_required"},
        )
    # Discord role assignment skipped: bot-only side effect.
    new = await db.register_player(DB_PATH, t["id"], user["discord_id"])
    return {"registered": True, "already": not new}


@app.delete("/api/tournaments/{tournament_id}/register")
async def unregister(tournament_id: int, user: CurrentUser) -> dict:
    t = await _tournament_or_404(tournament_id)
    await db.unregister_player(DB_PATH, t["id"], user["discord_id"])
    # Mirror the bot: also drop them from their tee time for this tournament.
    tt = await db.get_player_tee_time(DB_PATH, t["id"], user["discord_id"])
    if tt:
        await db.leave_tee_time(DB_PATH, tt["id"], user["discord_id"])
    return {"registered": False}


# --------------------------------------------------------------------------
# Tee times
# --------------------------------------------------------------------------
@app.get("/api/tournaments/{tournament_id}/tee-times")
async def list_tee_times(tournament_id: int, user: CurrentUser) -> list[dict]:
    t = await _tournament_or_404(tournament_id)
    rows = await db.list_tee_times(DB_PATH, t["id"])
    out = []
    for tt in rows:
        players = await db.get_tee_time_players(DB_PATH, tt["id"])
        out.append(_tee_time_json(tt, players))
    return out


@app.post("/api/tournaments/{tournament_id}/tee-times")
async def create_tee_time(
    tournament_id: int, body: TeeTimeCreate, user: CurrentUser
) -> dict:
    t = await _tournament_or_404(tournament_id)
    tz_name = await db.get_timezone(DB_PATH, user["discord_id"])
    if not tz_name:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "timezone_required"},
        )
    try:
        starts = parse_in_tz(body.date, body.time, tz_name)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)
        )
    tt_id = await db.create_tee_time(
        DB_PATH,
        t["id"],
        body.label,
        starts.isoformat(),
        body.max_players,
        user["discord_id"],
        None,  # channel_id: bot-only field
    )
    # Creator auto-joins their own tee time, like the bot.
    await db.join_tee_time(DB_PATH, tt_id, user["discord_id"])
    tt = await db.get_tee_time(DB_PATH, tt_id)
    players = await db.get_tee_time_players(DB_PATH, tt_id)
    return {**_tee_time_json(tt, players), "joined": True}


@app.post("/api/tee-times/{tee_time_id}/join")
async def join_tee_time(tee_time_id: int, user: CurrentUser) -> dict:
    tt, _ = await _tee_time_or_404(tee_time_id)
    result = await db.join_tee_time(DB_PATH, tt["id"], user["discord_id"])
    if result == "missing":  # pragma: no cover - checked above
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tee time not found"
        )
    if result == "full":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "tee_time_full"},
        )
    return {"joined": True, "already": result == "already"}


@app.post("/api/tee-times/{tee_time_id}/leave")
async def leave_tee_time(tee_time_id: int, user: CurrentUser) -> dict:
    tt, _ = await _tee_time_or_404(tee_time_id)
    await db.leave_tee_time(DB_PATH, tt["id"], user["discord_id"])
    return {"left": True}


@app.post("/api/tee-times/{tee_time_id}/request")
async def request_join(tee_time_id: int, user: CurrentUser) -> dict:
    tt, _ = await _tee_time_or_404(tee_time_id)
    result = await db.create_join_request(DB_PATH, tt["id"], user["discord_id"])
    if result == "missing":  # pragma: no cover - checked above
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tee time not found"
        )
    if result == "full":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "tee_time_full"},
        )
    if result == "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "request_pending"},
        )
    return {"requested": True, "already": result == "already"}


def _join_request_json(req: dict) -> dict:
    return {
        "id": req["id"],
        "tee_time_id": req["tee_time_id"],
        "player_discord_id": req["player_discord_id"],
        "status": req["status"],
        "created_at": req.get("created_at"),
        "decided_at": req.get("decided_at"),
        "decided_by": req.get("decided_by"),
    }


async def _creator_only(tt: dict, user: CurrentUser) -> None:
    if tt["created_by"] != user["discord_id"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the tee-time creator can do that",
        )


@app.get("/api/tee-times/{tee_time_id}/requests")
async def list_requests(tee_time_id: int, user: CurrentUser) -> list[dict]:
    tt, _ = await _tee_time_or_404(tee_time_id)
    await _creator_only(tt, user)
    rows = await db.list_pending_join_requests(DB_PATH, tt["id"])
    out = []
    for req in rows:
        player = await db.get_player(DB_PATH, req["player_discord_id"])
        out.append(
            {
                **_join_request_json(req),
                "display_name": db.display_name_of(player, req["player_discord_id"]),
            }
        )
    return out


async def _decide(tee_time_id: int, req_id: int, accept: bool, user: CurrentUser) -> dict:
    tt, t = await _tee_time_or_404(tee_time_id)
    await _creator_only(tt, user)
    req = await db.get_join_request(DB_PATH, req_id)
    if not req or req["tee_time_id"] != tt["id"]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Request not found"
        )
    if req["status"] != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "request_already_decided"},
        )
    decider = user["discord_id"]
    requester = req["player_discord_id"]
    if accept:
        # Mirror the bot's accept flow (_decide_join_request_flow): re-validate
        # everything, since the world may have changed while pending.
        if not await db.is_registered(DB_PATH, t["id"], requester):
            updated = await db.decide_join_request(DB_PATH, req_id, "declined", decider)
            return _join_request_json(updated)
        other = await db.get_player_tee_time(DB_PATH, t["id"], requester)
        if other and other["id"] != tt["id"]:
            updated = await db.decide_join_request(DB_PATH, req_id, "declined", decider)
            return _join_request_json(updated)
        join_result = await db.join_tee_time(DB_PATH, tt["id"], requester)
        if join_result == "full":
            updated = await db.decide_join_request(DB_PATH, req_id, "declined", decider)
            return _join_request_json(updated)
        # join_tee_time AND decide_join_request, exactly like the bot.
        updated = await db.decide_join_request(DB_PATH, req_id, "accepted", decider)
        return _join_request_json(updated)
    updated = await db.decide_join_request(DB_PATH, req_id, "declined", decider)
    return _join_request_json(updated)


@app.post("/api/tee-times/{tee_time_id}/requests/{req_id}/approve")
async def approve_request(
    tee_time_id: int, req_id: int, user: CurrentUser
) -> dict:
    return await _decide(tee_time_id, req_id, True, user)


@app.post("/api/tee-times/{tee_time_id}/requests/{req_id}/decline")
async def decline_request(
    tee_time_id: int, req_id: int, user: CurrentUser
) -> dict:
    return await _decide(tee_time_id, req_id, False, user)


# --------------------------------------------------------------------------
# Scorecards
# --------------------------------------------------------------------------
@app.get("/api/tee-times/{tee_time_id}/scorecard")
async def get_scorecard(tee_time_id: int, user: CurrentUser) -> dict:
    tt, t = await _tee_time_or_404(tee_time_id)
    # The caller's latest card *for this tee time* (get_latest_player_card is
    # tournament-wide, so filter down to this tee time).
    cards = await db.get_scorecards(DB_PATH, t["id"])
    mine = next(
        (
            c
            for c in reversed(cards)
            if c.get("player_discord_id") == user["discord_id"]
            and c.get("tee_time_id") == tt["id"]
        ),
        None,
    )
    if not mine:
        return {"card": None}
    return {"card": _card_json(mine, t.get("pars"))}


@app.put("/api/tee-times/{tee_time_id}/scorecard")
async def put_scorecard(
    tee_time_id: int, body: ScorecardSubmit, user: CurrentUser
) -> dict:
    tt, t = await _tee_time_or_404(tee_time_id)
    # Gate 1: the caller (submitter) must be in this tee time.
    mine = await db.get_player_tee_time(DB_PATH, t["id"], user["discord_id"])
    if not mine or mine["id"] != tt["id"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "not_in_tee_time"},
        )
    # Gate 2: the card owner must be in the same tee time.
    players = await db.get_tee_time_players(DB_PATH, tt["id"])
    if not any(p["discord_id"] == body.player_discord_id for p in players):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "player_not_in_tee_time"},
        )
    # Gate 3: complete card — one score per hole of the tournament.
    if len(body.scores) != t["holes"]:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Expected {t['holes']} hole scores but got {len(body.scores)}.",
        )
    # Gate 4: the tee time must have started (bot's tee-time gate).
    if not sl.tee_time_passed(tt.get("starts_at") or ""):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "tee_time_not_passed"},
        )
    # Verification rule from the bot: 2+ players in the tee time = partners
    # present -> auto-verified; solo rounds stay pending.
    player_count = await db.tee_time_player_count(DB_PATH, tt["id"])
    status_value = "verified" if player_count >= 2 else "pending"
    card_id = await db.upsert_scorecard(
        DB_PATH,
        t["id"],
        body.player_discord_id,
        None,  # team_id: the mobile scorecard flow is player-level
        tt["id"],
        body.scores,
        status_value,
        submitted_by=user["discord_id"],
    )
    card = await db.get_scorecard(DB_PATH, card_id)
    return {"card": _card_json(card, t.get("pars"))}


# --------------------------------------------------------------------------
# Leaderboard
# --------------------------------------------------------------------------
@app.get("/api/tournaments/{tournament_id}/leaderboard")
async def leaderboard(tournament_id: int, user: CurrentUser) -> dict:
    t = await _tournament_or_404(tournament_id)
    pars = _parse_pars(t.get("pars"))
    base = {
        "tournament_id": t["id"],
        "name": t["name"],
        "format": t["format"],
        "holes": t["holes"],
        "course": t["course"],
        "status": t["status"],
        "settings": sl.format_settings(t),
    }
    fmt = t["format"]
    if fmt == "stroke":
        # Same ranking helpers the bot uses (lr._stroke_ranked -> sl.rank_stroke).
        ranked, pending = await lr._stroke_ranked(DB_PATH, t)

        async def _row(i: int, c: dict) -> dict:
            player = await db.get_player(DB_PATH, c["player_discord_id"])
            return {
                "position": i,
                "discord_id": c["player_discord_id"],
                "display_name": db.display_name_of(player, c["player_discord_id"]),
                "total": c["total"],
                "to_par": sl.to_par(c["total"], pars),
                "to_par_display": sl.format_to_par(sl.to_par(c["total"], pars)),
                "status": c["status"],
            }

        return {
            **base,
            "standings": [await _row(i, c) for i, c in enumerate(ranked, 1)],
            "pending": [await _row(0, c) for c in pending],
        }
    if fmt in ("best_ball", "alt_shot", "scramble"):
        # Same ranking the bot uses (lr._team_rows -> sl.rank_scramble etc.).
        rows, scoreless = await lr._team_rows(DB_PATH, t)
        standings = [
            {
                "position": i,
                "team_id": r["team_id"],
                "team_name": r["name"],
                "total": r["total"],
                "to_par": sl.to_par(r["total"], pars),
                "to_par_display": sl.format_to_par(sl.to_par(r["total"], pars)),
                "players": r["members"],
                "pending": r["pending"],
            }
            for i, r in enumerate(rows, 1)
        ]
        return {**base, "standings": standings, "no_scores_yet": scoreless}
    if fmt == "match":
        matches = await db.list_matches(DB_PATH, t["id"], status="confirmed")
        records = sl.rank_match_records(sl.match_records(matches))
        standings = []
        for i, (pid, rec) in enumerate(records, 1):
            player = await db.get_player(DB_PATH, pid)
            standings.append(
                {
                    "position": i,
                    "discord_id": pid,
                    "display_name": db.display_name_of(player, pid),
                    "wins": rec["w"],
                    "losses": rec["l"],
                    "ties": rec["t"],
                }
            )
        return {**base, "standings": standings}
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=f"Unknown tournament format: {fmt}",
    )


# --------------------------------------------------------------------------
# Seasons
# --------------------------------------------------------------------------
@app.get("/api/seasons/standings")
async def season_standings(user: CurrentUser) -> dict:
    season = await db.get_active_season(DB_PATH, GUILD_ID)
    if not season:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "no_active_season"},
        )
    rows = await db.get_season_standings(DB_PATH, season["id"])
    return {
        "season": {
            "id": season["id"],
            "name": season["name"],
            "status": season["status"],
        },
        "standings": [
            {
                "discord_id": r["discord_id"],
                "display_name": r["display_name"],
                "golfplus_handle": r.get("golfplus_handle"),
                "total_points": r["total_points"],
                "tournaments_played": r["tournaments_played"],
            }
            for r in rows
        ],
    }


# --------------------------------------------------------------------------
# Player profile
# --------------------------------------------------------------------------
def _profile_json(row: dict) -> dict:
    return {
        "discord_id": row["discord_id"],
        "display_name": row["display_name"],
        "golfplus_handle": row.get("golfplus_handle"),
        "timezone": row.get("timezone"),
    }


@app.get("/api/players/me")
async def get_me(user: CurrentUser) -> dict:
    return _profile_json(user)


@app.patch("/api/players/me")
async def update_me(body: PlayerUpdate, user: CurrentUser) -> dict:
    pid = user["discord_id"]
    if body.timezone is not None:
        # Mirror /set_timezone: any valid IANA name, 422 on invalid.
        try:
            ZoneInfo(body.timezone.strip())
        except (ZoneInfoNotFoundError, ValueError):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"'{body.timezone}' is not a valid IANA timezone name.",
            )
        await db.set_timezone(DB_PATH, pid, body.timezone.strip())
    if body.golfplus_handle is not None:
        handle = body.golfplus_handle.strip()
        # Empty string -> NULL (unlink), like the bot's /unlink_golfplus.
        await db.set_golfplus_handle(DB_PATH, pid, handle or None)
    return _profile_json(await db.get_player(DB_PATH, pid))


@app.get("/api/players/me/stats")
async def my_stats(user: CurrentUser) -> dict:
    # Mirror the /stats command: verified individual cards -> sl.career_stats.
    pid = user["discord_id"]
    cards = await db.get_player_verified_cards(DB_PATH, GUILD_ID, pid)
    rounds = []
    for c in cards:
        try:
            holes = json.loads(c["holes_json"])
        except (ValueError, TypeError):
            continue
        if not holes:
            continue
        rounds.append({"holes": holes, "pars": _parse_pars(c.get("pars"))})
    s = sl.career_stats(rounds)
    matches = await db.list_confirmed_matches_for_player(DB_PATH, GUILD_ID, pid)
    rec = sl.match_records(matches).get(pid, {"w": 0, "l": 0, "t": 0})
    return {
        "rounds_played": s["rounds_played"],
        "best_total": s["best_total"],
        "best_to_par": s["best_to_par"],
        "best_to_par_display": sl.format_to_par(s["best_to_par"]),
        "avg_total": s["avg_total"],
        "birdies_or_better": s["birdies_or_better"],
        "pars_made": s["pars_made"],
        "bogeys": s["bogeys"],
        "doubles_or_worse": s["doubles_or_worse"],
        "best_par_streak": s["best_par_streak"],
        "match_record": {"wins": rec["w"], "losses": rec["l"], "ties": rec["t"]},
        "confirmed_matches": len(matches),
    }


# --------------------------------------------------------------------------
# Run
# --------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8420")))

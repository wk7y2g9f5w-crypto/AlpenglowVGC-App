"""Alpenglow VGC mobile companion API.

Read-only+write JSON bridge over the Discord tournament bot's SQLite
database. Reuses the bot's own modules (``src.db``, ``src.scoring_logic``,
``src.leaderboard_render``, and pure helpers from ``src.cogs``) so the app
and Discord always agree.

This service NEVER modifies the bot's behavior: it shares the one SQLite
file, keeps transactions short (the bot's db layer opens a fresh connection
per call), and never writes to Discord (channels/messages/roles are
bot-only). The one exception is a read-only lookup of the caller's guild
roles via Discord's REST API, used solely to gate admin-only endpoints —
mirroring the bot's own admin/mod/Tournament Director check.

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
from src import golfplus_courses as gc  # noqa: E402
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
from pydantic import BaseModel, Field, field_validator, model_validator  # noqa: E402


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
# Crew (admin/mod) gating
# --------------------------------------------------------------------------
# Mirrors the bot's admin model (cogs/common.py is_admin): the "Tournament
# Admin" role, the "Tournament Director" role, or Manage Server permission —
# plus the "Mod" and "Admin" crew roles, since the app's admin surface is
# meant for any admin/moderator. Read-only Discord REST lookup; the API
# never writes to Discord.
CREW_ROLE_NAMES = frozenset(
    {"Tournament Admin", "Admin", "Mod", "Tournament Director"}
)
_MANAGE_GUILD_BIT = 1 << 5


async def _fetch_guild_standing(
    discord_id: str,
) -> tuple[bool, set[str]] | None:
    """Guild standing for a Discord user: (privileged, role_names).

    privileged = guild owner or holds the Manage Server permission.
    Returns None when Discord couldn't be reached or the user wasn't found.
    """
    bot_token = os.environ.get("DISCORD_TOKEN")
    guild_id = os.environ.get("GUILD_ID") or str(config.GUILD_ID or "")
    if not bot_token or not guild_id:
        return None
    headers = {"Authorization": f"Bot {bot_token}"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            member_resp = await client.get(
                f"https://discord.com/api/v10/guilds/{guild_id}"
                f"/members/{discord_id}",
                headers=headers,
            )
            if member_resp.status_code != 200:
                return None
            member = member_resp.json()
            privileged = False
            # Guild owner implicitly holds every permission (Administrator),
            # but carries no explicit roles — recognize them directly.
            try:
                guild_resp = await client.get(
                    f"https://discord.com/api/v10/guilds/{guild_id}",
                    headers=headers,
                )
                if guild_resp.status_code == 200 and str(
                    guild_resp.json().get("owner_id")
                ) == str(discord_id):
                    privileged = True
            except (httpx.HTTPError, ValueError, TypeError, KeyError):
                pass
            try:
                if int(member.get("permissions", "0")) & _MANAGE_GUILD_BIT:
                    privileged = True
            except (TypeError, ValueError):
                pass
            roles_resp = await client.get(
                f"https://discord.com/api/v10/guilds/{guild_id}/roles",
                headers=headers,
            )
            if roles_resp.status_code != 200:
                return None
            member_role_ids = set(member.get("roles") or [])
            names = {
                r["name"]
                for r in roles_resp.json()
                if r.get("id") in member_role_ids
            }
            return privileged, names
    except httpx.HTTPError:
        return None
    except (ValueError, TypeError, KeyError):
        return None


async def fetch_crew_status(discord_id: str) -> bool | None:
    """Is this Discord user crew (admin/mod/director) in the guild?

    Returns True/False when Discord answered, None when the check could not
    be performed (no bot token configured, network/Discord failure). Single
    function so unit tests can monkeypatch it.
    """
    standing = await _fetch_guild_standing(discord_id)
    if standing is None:
        return None
    privileged, names = standing
    return privileged or bool(names & CREW_ROLE_NAMES)


# Full admins only: mods and tournament directors are deliberately excluded.
ADMIN_ROLE_NAMES = frozenset({"Tournament Admin", "Admin"})


async def fetch_admin_status(discord_id: str) -> bool | None:
    """Is this Discord user a full admin (owner / Manage Server / Admin)?

    Used for destructive actions like tournament delete.
    """
    standing = await _fetch_guild_standing(discord_id)
    if standing is None:
        return None
    privileged, names = standing
    return privileged or bool(names & ADMIN_ROLE_NAMES)


async def require_crew(user: CurrentUser) -> dict:
    """Dependency: 403 unless the caller is crew, 503 when unverifiable."""
    ok = await fetch_crew_status(user["discord_id"])
    if ok is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not verify crew status — try again shortly.",
        )
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Crew only: requires an admin/moderator role.",
        )
    return user


CrewUser = Annotated[dict, Depends(require_crew)]


async def require_admin_user(user: CurrentUser) -> dict:
    """Dependency: 403 unless the caller is a full admin, 503 when
    unverifiable. Mods and tournament directors do NOT pass."""
    ok = await fetch_admin_status(user["discord_id"])
    if ok is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not verify admin status — try again shortly.",
        )
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin only: requires the Admin role.",
        )
    return user


AdminUser = Annotated[dict, Depends(require_admin_user)]


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
        "round_number": tt.get("round_number") or 1,
        "players": [_player_json(p) for p in players],
    }


def _card_json(card: dict, pars_csv: str | None) -> dict:
    scores = json.loads(card["holes_json"])
    pars = _parse_pars(pars_csv)
    thru = sum(1 for s in scores if s is not None)
    return {
        "player_discord_id": card["player_discord_id"],
        "round_number": card.get("round_number") or 1,
        "scores": scores,
        "total": card["total"],
        "to_par": sl.to_par(card["total"], pars),
        "thru": thru,
        "status": card["status"],
        "submitted_by": card.get("submitted_by"),
        "witness_name": card.get("witness_name"),
    }


def _tournament_json(t: dict, registered: bool,
                     rounds: list[dict] | None = None) -> dict:
    pars_csv = t.get("pars")
    pars = None
    if pars_csv:
        try:
            pars = [int(p) for p in str(pars_csv).split(",") if p.strip()]
        except ValueError:
            pars = None
    rounds = rounds or []
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
        "pars": pars,
        "registered": registered,
        "num_rounds": len(rounds) or 1,
        "rounds": [
            {
                "round_number": r["round_number"],
                "tee_position": r["tee_position"],
                "pin_position": r["pin_position"],
                "wind_strength": r["wind_strength"],
                "start_date": r.get("start_date"),
                "end_date": r.get("end_date"),
            }
            for r in rounds
        ],
    }


# --------------------------------------------------------------------------
# Request schemas
# --------------------------------------------------------------------------
class TeeTimeCreate(BaseModel):
    label: str
    date: str  # YYYY-MM-DD
    time: str  # HH:MM 24h
    max_players: int = Field(default=4, ge=1, le=4)
    round_number: int = Field(default=1, ge=1, le=5)

    @field_validator("label")
    @classmethod
    def _label_nonempty(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("label must be non-empty")
        return v[:80]


class TeeTimeUpdate(BaseModel):
    """Edit a tee time: any subset of label/date/time/round. At least one required."""
    label: str | None = None
    date: str | None = None  # YYYY-MM-DD
    time: str | None = None  # HH:MM 24h
    round_number: int | None = Field(default=None, ge=1, le=5)

    @field_validator("label")
    @classmethod
    def _label_clean(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            raise ValueError("label must be non-empty")
        return v[:80]

    @model_validator(mode="after")
    def _at_least_one(self):
        if self.label is None and self.date is None and self.time is None:
            raise ValueError("nothing to change: pass label, date or time")
        return self

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


class TournamentUpdate(BaseModel):
    """Edit a tournament: any subset of name/description/dates/course.

    Format, holes and rounds are intentionally NOT editable — changing them
    would corrupt existing scorecards.
    """

    name: str | None = None
    description: str | None = None
    start_date: str | None = None  # YYYY-MM-DD
    end_date: str | None = None  # YYYY-MM-DD
    course: str | None = None

    @field_validator("name", "course")
    @classmethod
    def _nonblank_str(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            raise ValueError("must be non-empty")
        return v[:80]

    @field_validator("description")
    @classmethod
    def _description_clean(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return v.strip()[:500] or None

    @field_validator("start_date", "end_date")
    @classmethod
    def _valid_date(cls, v: str | None) -> str | None:
        if v is None:
            return None
        # Same semantics as the bot: sl.parse_date raises ValueError on bad input.
        sl.parse_date(v)
        return v.strip()

    @model_validator(mode="after")
    def _something_to_change(self):
        if all(
            v is None
            for v in (self.name, self.description, self.start_date,
                      self.end_date, self.course)
        ):
            raise ValueError("nothing to change: pass at least one field")
        return self


class ScorecardSubmit(BaseModel):
    player_discord_id: str
    # Full-length list; None marks a hole not yet played (live entry).
    scores: list[int | None]
    round_number: int = Field(default=1, ge=1, le=5)
    witness_name: str | None = Field(default=None, max_length=80)
    # complete=False: live partial save (in_progress). complete=True: final
    # submission — every hole must have a score.
    complete: bool = False

    @field_validator("witness_name")
    @classmethod
    def _witness_clean(cls, v: str | None) -> str | None:
        v = (v or "").strip()
        return v[:80] or None

    @field_validator("scores")
    @classmethod
    def _scores_in_range(cls, v: list[int | None]) -> list[int | None]:
        for s in v:
            if s is None:
                continue
            if not isinstance(s, int) or isinstance(s, bool) or not 1 <= s <= 15:
                raise ValueError(f"Score {s} is out of range — holes are scored 1-15.")
        return v


class PlayerUpdate(BaseModel):
    timezone: str | None = None
    golfplus_handle: str | None = None


class RoundCreate(BaseModel):
    """Per-round Golf+ settings and dates. Round numbers are implied by list order."""

    tee_position: str = "middle"
    pin_position: str = "white"
    wind_strength: str = "moderate"
    # Optional per-round window; when omitted the server splits the
    # tournament's overall date range evenly across rounds.
    start_date: str | None = None
    end_date: str | None = None

    @field_validator("tee_position")
    @classmethod
    def _tee(cls, v: str) -> str:
        v = (v or "").strip()
        if v not in ("front", "middle", "back"):
            raise ValueError(f"Unknown tee position '{v}'")
        return v

    @field_validator("pin_position")
    @classmethod
    def _pin(cls, v: str) -> str:
        v = (v or "").strip()
        if v not in ("black", "white", "red"):
            raise ValueError(f"Unknown pin position '{v}'")
        return v

    @field_validator("wind_strength")
    @classmethod
    def _wind(cls, v: str) -> str:
        v = (v or "").strip()
        if v not in ("low", "moderate", "severe"):
            raise ValueError(f"Unknown wind strength '{v}'")
        return v


class TournamentCreate(BaseModel):
    """Mirrors /tournament create: same fields, same validation, same defaults."""

    name: str
    format: str  # validated against the bot's FORMAT ids below
    holes: int
    course: str
    start_date: str  # YYYY-MM-DD
    end_date: str  # YYYY-MM-DD
    tee_position: str = "middle"
    pin_position: str = "white"
    wind_strength: str = "moderate"
    green_speed: str = "pro"
    pars: str | None = None  # comma-separated override; auto-filled when omitted
    description: str | None = None
    # 1-5 rounds; each is one full play of the course (18 holes).
    # When omitted, a single round copies the top-level tee/pin/wind.
    rounds: list[RoundCreate] | None = None

    @model_validator(mode="after")
    def _rounds_valid(self) -> "TournamentCreate":
        if self.rounds is not None:
            if not 1 <= len(self.rounds) <= 5:
                raise ValueError("rounds must have 1-5 entries")
            if self.format == "match" and len(self.rounds) > 1:
                raise ValueError("match play tournaments are single-round")
            if len(self.rounds) > 1 and self.holes != 18:
                raise ValueError("multi-round tournaments are 18 holes per round")
        return self

    @field_validator("name", "course")
    @classmethod
    def _nonempty(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("must be non-empty")
        return v[:80]

    @field_validator("format")
    @classmethod
    def _known_format(cls, v: str) -> str:
        v = (v or "").strip()
        if v not in ("stroke", "match", "best_ball", "alt_shot", "scramble"):
            raise ValueError(f"Unknown format '{v}'")
        return v

    @field_validator("holes")
    @classmethod
    def _nine_or_eighteen(cls, v: int) -> int:
        if v not in (9, 18):
            raise ValueError("holes must be 9 or 18")
        return v

    @field_validator("tee_position")
    @classmethod
    def _tee(cls, v: str) -> str:
        v = (v or "").strip()
        if v not in ("front", "middle", "back"):
            raise ValueError(f"Unknown tee position '{v}'")
        return v

    @field_validator("pin_position")
    @classmethod
    def _pin(cls, v: str) -> str:
        v = (v or "").strip()
        if v not in ("black", "white", "red"):
            raise ValueError(f"Unknown pin position '{v}'")
        return v

    @field_validator("wind_strength")
    @classmethod
    def _wind(cls, v: str) -> str:
        v = (v or "").strip()
        if v not in ("low", "moderate", "severe"):
            raise ValueError(f"Unknown wind strength '{v}'")
        return v

    @field_validator("green_speed")
    @classmethod
    def _green(cls, v: str) -> str:
        v = (v or "").strip()
        if v not in ("veryfast", "pro"):
            raise ValueError(f"Unknown green speed '{v}'")
        return v

    @field_validator("description")
    @classmethod
    def _desc_len(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return v.strip()[:500] or None


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
# OAuth intermediary redirect (mobile login)
# --------------------------------------------------------------------------
# Discord does not accept custom URL schemes (myapp://...) as OAuth redirect
# URIs. Register this https URL in the Discord Developer Portal instead; it
# bounces the browser back into the app via the custom scheme, preserving
# the query string (?code=... / ?error=...).
OAUTH_APP_REDIRECT = os.environ.get(
    "OAUTH_APP_REDIRECT", "com.alpenglow.vgc.app://oauth-callback"
)


@app.get("/oauth/callback")
async def oauth_callback(request: Request):
    """Bounce Discord's OAuth response back into the mobile app.

    No auth required — this is a plain browser redirect. The redirect target
    is server-controlled (OAUTH_APP_REDIRECT); only the query string is
    passed through, so this cannot be abused as an open redirect.
    """
    from fastapi.responses import RedirectResponse

    qs = str(request.query_params)
    target = f"{OAUTH_APP_REDIRECT}?{qs}" if qs else OAUTH_APP_REDIRECT
    return RedirectResponse(url=target, status_code=302)


# --------------------------------------------------------------------------
# Tournaments
# --------------------------------------------------------------------------
@app.get("/api/courses")
async def list_courses(user: CurrentUser) -> list[dict]:
    """Golf+ course catalog with official hole-by-hole pars.

    Used by the app's tournament-creation picker and score-entry
    indicators. Pars come from the bot's verified course database.
    """
    return [
        {
            "name": name,
            "pars": list(gc.COURSE_PARS[name]),
            "par_total": sum(gc.COURSE_PARS[name]),
        }
        for name in gc.COURSES
    ]


@app.get("/api/tournaments")
async def list_tournaments(user: CurrentUser) -> list[dict]:
    rows = await db.list_tournaments(DB_PATH, GUILD_ID)
    out = []
    for t in rows:
        registered = await db.is_registered(DB_PATH, t["id"], user["discord_id"])
        rounds = await db.list_rounds(DB_PATH, t["id"])
        out.append(_tournament_json(t, registered, rounds))
    return out


def _validated_round(r: "RoundCreate", body: "TournamentCreate") -> dict:
    """Validate one RoundCreate's date window and return its db dict.

    Raises HTTPException(400) on a bad range or a window outside the
    tournament's overall dates. Nones fall back to the even-split default
    in db.create_tournament.
    """
    try:
        start_iso, end_iso = sl.validate_round_dates(
            r.start_date, r.end_date, body.start_date, body.end_date)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        )
    return {
        "tee_position": r.tee_position,
        "pin_position": r.pin_position,
        "wind_strength": r.wind_strength,
        "start_date": start_iso,
        "end_date": end_iso,
    }


@app.post("/api/tournaments", status_code=status.HTTP_201_CREATED)
async def create_tournament(body: TournamentCreate, user: CrewUser) -> dict:
    """Create a tournament (crew only). Mirrors /tournament create.

    Pars auto-fill from the course database for known courses, exactly
    like the bot. The bot picks the new tournament up from the outbox
    within a minute: it attaches the persistent Register button and
    refreshes the Tee Sheet board.
    """
    try:
        start_d, end_d = sl.validate_date_range(body.start_date, body.end_date)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        )

    pars_clean = None
    if body.pars:
        try:
            parsed = sl.parse_pars(body.pars, body.holes)
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Bad pars: {e}",
            )
        pars_clean = ",".join(str(p) for p in parsed)
    else:
        auto = gc.course_pars(body.course, body.holes)
        if auto:
            pars_clean = ",".join(str(p) for p in auto)

    tid = await db.create_tournament(
        DB_PATH,
        GUILD_ID,
        body.name,
        body.format,
        body.holes,
        body.course,
        pars_clean,
        body.description,
        user["discord_id"],
        start_date=start_d.isoformat(),
        end_date=end_d.isoformat(),
        tee_position=body.tee_position,
        pin_position=body.pin_position,
        wind_strength=body.wind_strength,
        green_speed=body.green_speed,
        rounds=(
            [_validated_round(r, body) for r in body.rounds]
            if body.rounds
            else None
        ),
    )
    await db.enqueue_outbox(DB_PATH, "tournament_created", {"tournament_id": tid})
    t = await db.get_tournament(DB_PATH, tid)
    rounds = await db.list_rounds(DB_PATH, tid)
    return _tournament_json(t, registered=False, rounds=rounds)


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
    # Mirror the bot: also drop them from all their tee times for this
    # tournament.
    await db.leave_all_tee_times(DB_PATH, t["id"], user["discord_id"])
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
    if not await db.get_round(DB_PATH, t["id"], body.round_number):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Round {body.round_number} doesn't exist in this tournament.",
        )
    # One tee time per player per round — no exceptions.
    conflict = await db.tee_time_for_round(
        DB_PATH, t["id"], body.round_number, user["discord_id"]
    )
    if conflict is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "round_conflict",
                "message": (
                    f"You're already in {conflict['label']} for "
                    f"Round {body.round_number} — leave it first to create "
                    "a different one."
                ),
            },
        )
    tt_id = await db.create_tee_time(
        DB_PATH,
        t["id"],
        body.label,
        starts.isoformat(),
        body.max_players,
        user["discord_id"],
        None,  # channel_id: bot-only field
        round_number=body.round_number,
    )
    # Creator auto-joins their own tee time, like the bot.
    await db.join_tee_time(DB_PATH, tt_id, user["discord_id"])
    tt = await db.get_tee_time(DB_PATH, tt_id)
    players = await db.get_tee_time_players(DB_PATH, tt_id)
    return {**_tee_time_json(tt, players), "joined": True}


@app.post("/api/tee-times/{tee_time_id}/join")
async def join_tee_time(tee_time_id: int, user: CurrentUser) -> dict:
    tt, t = await _tee_time_or_404(tee_time_id)
    conflict = await db.tee_time_for_round(
        DB_PATH, t["id"], tt.get("round_number") or 1, user["discord_id"],
        exclude_tee_time_id=tt["id"],
    )
    if conflict is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "round_conflict",
                "message": (
                    f"You're already in {conflict['label']} for "
                    f"Round {conflict.get('round_number') or 1} — leave it "
                    "first to join this one."
                ),
            },
        )
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
    if result == "round_conflict":  # pragma: no cover - pre-checked above
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "round_conflict"},
        )
    return {"joined": True, "already": result == "already"}


@app.post("/api/tee-times/{tee_time_id}/leave")
async def leave_tee_time(tee_time_id: int, user: CurrentUser) -> dict:
    tt, _ = await _tee_time_or_404(tee_time_id)
    await db.leave_tee_time(DB_PATH, tt["id"], user["discord_id"])
    return {"left": True}


async def _can_manage_tee_time(tt: dict, user: CurrentUser) -> bool:
    """Only the tee time creator or crew may edit/delete it."""
    if tt["created_by"] == user["discord_id"]:
        return True
    return bool(await fetch_crew_status(user["discord_id"]))


@app.patch("/api/tee-times/{tee_time_id}")
async def update_tee_time(
    tee_time_id: int, body: TeeTimeUpdate, user: CurrentUser
) -> dict:
    tt, t = await _tee_time_or_404(tee_time_id)
    if not await _can_manage_tee_time(tt, user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the tee time creator or crew can edit it.",
        )
    if body.round_number is not None and not await db.get_round(
        DB_PATH, t["id"], body.round_number
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Round {body.round_number} doesn't exist in this tournament.",
        )
    starts_at = None
    if body.date is not None or body.time is not None:
        tz_name = await db.get_timezone(DB_PATH, user["discord_id"])
        if not tz_name:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "timezone_required"},
            )
        try:
            viewer_tz = ZoneInfo(tz_name)
        except ZoneInfoNotFoundError:
            viewer_tz = timezone.utc
        cur = datetime.fromisoformat(tt["starts_at"]).astimezone(viewer_tz)
        try:
            starts = parse_in_tz(
                body.date or cur.strftime("%Y-%m-%d"),
                body.time or cur.strftime("%H:%M"),
                tz_name,
            )
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)
            )
        starts_at = starts.isoformat()
    changed = await db.update_tee_time(
        DB_PATH, tt["id"], label=body.label, starts_at=starts_at,
        round_number=body.round_number,
    )
    if not changed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tee time not found",
        )
    tt = await db.get_tee_time(DB_PATH, tt["id"])
    players = await db.get_tee_time_players(DB_PATH, tt["id"])
    return _tee_time_json(tt, players)


@app.delete("/api/tee-times/{tee_time_id}")
async def delete_tee_time(tee_time_id: int, user: CurrentUser) -> dict:
    tt, _ = await _tee_time_or_404(tee_time_id)
    if not await _can_manage_tee_time(tt, user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the tee time creator or crew can delete it.",
        )
    n_cards = await db.count_tee_time_scorecards(DB_PATH, tt["id"])
    if n_cards:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "tee_time_has_scores",
                "message": f"This tee time has {n_cards} submitted scorecard(s) — delete those scores first.",
            },
        )
    await db.delete_tee_time(DB_PATH, tt["id"])
    return {"deleted": True}


@app.patch("/api/tournaments/{tournament_id}")
async def edit_tournament(
    tournament_id: int, patch: TournamentUpdate, user: CrewUser
) -> dict:
    t = await _tournament_or_404(tournament_id)
    new_start = patch.start_date or t.get("start_date")
    new_end = patch.end_date or t.get("end_date")
    try:
        sl.validate_date_range(new_start, new_end)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    changed = await db.update_tournament(
        DB_PATH, t["id"],
        name=patch.name,
        description=patch.description,
        start_date=patch.start_date,
        end_date=patch.end_date,
        course=patch.course,
    )
    t = await db.get_tournament(DB_PATH, t["id"])
    rounds = await db.list_rounds(DB_PATH, t["id"])
    return _tournament_json(t, registered=False, rounds=rounds)


class RoundUpdate(BaseModel):
    """Patch a round's Golf+ settings and/or date window (crew only)."""

    tee_position: str | None = None
    pin_position: str | None = None
    wind_strength: str | None = None
    start_date: str | None = None  # YYYY-MM-DD
    end_date: str | None = None  # YYYY-MM-DD


@app.patch("/api/tournaments/{tournament_id}/rounds/{round_number}")
async def update_round(
    tournament_id: int, round_number: int, patch: RoundUpdate, user: CrewUser
) -> dict:
    t = await _tournament_or_404(tournament_id)
    rnd = await db.get_round(DB_PATH, t["id"], round_number)
    if rnd is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Round {round_number} doesn't exist in this tournament.",
        )
    new_start = patch.start_date if patch.start_date is not None else rnd.get("start_date")
    new_end = patch.end_date if patch.end_date is not None else rnd.get("end_date")
    try:
        start_iso, end_iso = sl.validate_round_dates(
            new_start, new_end, t.get("start_date"), t.get("end_date"))
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    for field, allowed in (
        ("tee_position", ("front", "middle", "back")),
        ("pin_position", ("black", "white", "red")),
        ("wind_strength", ("low", "moderate", "severe")),
    ):
        v = getattr(patch, field)
        if v is not None and v not in allowed:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown {field} '{v}'.",
            )
    rnd = await db.update_round(
        DB_PATH, t["id"], round_number,
        tee_position=patch.tee_position,
        pin_position=patch.pin_position,
        wind_strength=patch.wind_strength,
        start_date=start_iso, end_date=end_iso,
    )
    rounds = await db.list_rounds(DB_PATH, t["id"])
    return _tournament_json(t, registered=False, rounds=rounds)


async def _finish_tournament(t: dict, award_points: bool) -> dict:
    """Shared finalize for complete (points + standings post) and end
    (silent close)."""
    if t["status"] == "completed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Tournament is already completed.",
        )
    await db.set_tournament_status(DB_PATH, t["id"], "completed")
    await db.expire_join_requests_for_tournament(DB_PATH, t["id"])
    season_msg = ""
    if award_points:
        try:
            points_rows = await lr.final_standings_points(DB_PATH, t["id"])
            seasons = await db.get_seasons_for_tournament(
                DB_PATH, t["id"], "active")
            awarded = 0
            for s in seasons:
                awarded += await db.record_season_points(
                    DB_PATH, s["id"], t["id"],
                    [(r["player_discord_id"], r["position"], r["points"])
                     for r in points_rows],
                )
            if seasons:
                names = ", ".join(s["name"] for s in seasons)
                season_msg = (f" Season points awarded to {awarded} player(s)"
                              f" in {names}.")
        except Exception as e:  # never break the finalize itself
            print(f"season points award failed for tournament {t['id']}: {e}")
            season_msg = " (season points could not be awarded — check the logs)"
    return {"completed": True, "season_msg": season_msg}


@app.post("/api/tournaments/{tournament_id}/complete")
async def complete_tournament(tournament_id: int, user: CrewUser) -> dict:
    """Finalize a tournament: final standings post in #event-signups +
    season points. The bot's outbox drain posts the leaderboard."""
    t = await _tournament_or_404(tournament_id)
    result = await _finish_tournament(t, award_points=True)
    await db.enqueue_outbox(
        DB_PATH, "tournament_completed",
        {"tournament_id": t["id"], "guild_id": GUILD_ID})
    return result


@app.post("/api/tournaments/{tournament_id}/end")
async def end_tournament(tournament_id: int, user: CrewUser) -> dict:
    """Close a tournament immediately — no final standings, no season points.
    For events that fizzle out. Use /complete for the full finish."""
    t = await _tournament_or_404(tournament_id)
    result = await _finish_tournament(t, award_points=False)
    await db.enqueue_outbox(
        DB_PATH, "tournament_ended",
        {"tournament_id": t["id"], "guild_id": GUILD_ID})
    return result


@app.delete("/api/tournaments/{tournament_id}")
async def delete_tournament(tournament_id: int, user: AdminUser) -> dict:
    """Permanently delete a tournament and everything under it.

    Admins only — mods and tournament directors are refused.
    """
    t = await _tournament_or_404(tournament_id)
    counts = await db.tournament_usage_counts(DB_PATH, t["id"])
    await db.delete_tournament(DB_PATH, t["id"])
    return {"deleted": True, "counts": counts}


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
        other = await db.tee_time_for_round(
            DB_PATH, t["id"], tt.get("round_number") or 1, requester,
            exclude_tee_time_id=tt["id"],
        )
        if other is not None:
            updated = await db.decide_join_request(DB_PATH, req_id, "declined", decider)
            return _join_request_json(updated)
        join_result = await db.join_tee_time(DB_PATH, tt["id"], requester)
        if join_result in ("full", "round_conflict"):
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
async def get_scorecard(
    tee_time_id: int,
    user: CurrentUser,
    round_number: int | None = None,
    player_discord_id: str | None = None,
) -> dict:
    tt, t = await _tee_time_or_404(tee_time_id)
    target_id = player_discord_id or user["discord_id"]
    if player_discord_id is not None:
        # Only cards for players in this tee time are visible here.
        tt_players = await db.get_tee_time_players(DB_PATH, tt["id"])
        if not any(p["discord_id"] == target_id for p in tt_players):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "player_not_in_tee_time"},
            )
    # The target's latest card *for this tee time* (get_latest_player_card is
    # tournament-wide, so filter down to this tee time).
    cards = await db.get_scorecards(DB_PATH, t["id"])
    mine = next(
        (
            c
            for c in reversed(cards)
            if c.get("player_discord_id") == target_id
            and c.get("tee_time_id") == tt["id"]
            and (round_number is None
                 or (c.get("round_number") or 1) == round_number)
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
    if not await db.is_player_in_tee_time(
        DB_PATH, tt["id"], user["discord_id"]
    ):
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
    # Gate 3: the card is one score per hole of the tournament (nulls
    # allowed for live partial saves); a final submission needs every hole.
    if len(body.scores) != t["holes"]:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Expected {t['holes']} hole scores but got {len(body.scores)}.",
        )
    if body.complete and any(s is None for s in body.scores):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "scorecard_incomplete",
                "message": "Every hole needs a score before the card can be submitted.",
            },
        )
    # Gate 3b: the round must exist on this tournament.
    rnd = await db.get_round(DB_PATH, t["id"], body.round_number)
    if rnd is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Round {body.round_number} does not exist for this tournament.",
        )
    # Gate 3c: the round's window must have opened — nobody plays a round
    # that hasn't started yet.
    if not sl.round_has_started(rnd.get("start_date")):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "round_not_started",
                "message": (
                    f"Round {body.round_number} hasn't started yet — it opens "
                    f"{sl.format_date(rnd.get('start_date'))}."
                ),
            },
        )
    # Gate 3d: hard cutoff at the round's end date — crew can still submit
    # after the window closes (e.g. entering a missed card).
    is_crew: bool | None = None
    if sl.round_has_ended(rnd.get("end_date")):
        is_crew = await fetch_crew_status(user["discord_id"])
        if is_crew is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Could not verify crew status with Discord — try again shortly.",
            )
        if not is_crew:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "round_ended",
                    "message": (
                        f"Round {body.round_number} closed "
                        f"{sl.format_date(rnd.get('end_date'))} — scores are "
                        "locked. Ask a crew member if a card still needs "
                        "to go in."
                    ),
                },
            )
    # Gate 3e: one scorecard per round per member — a card already submitted
    # for this round in another tee time blocks a second one.
    round_card = await db.find_round_card(
        DB_PATH, t["id"], body.player_discord_id, body.round_number
    )
    if round_card is not None and round_card["tee_time_id"] != tt["id"]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "round_already_submitted",
                "message": (
                    f"A card for Round {body.round_number} was already "
                    f"submitted in {round_card['tee_time_label']} — one "
                    "scorecard per round per player."
                ),
            },
        )
    # Gate 4: the tee time must have started (bot's tee-time gate).
    if not sl.tee_time_passed(tt.get("starts_at") or ""):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "tee_time_not_passed"},
        )
    # Gate 5: a COMPLETED card can only be changed by crew (admins, mods,
    # tournament directors). Finalizing your own in-progress card, or a
    # first submission, stays open to tee-time members.
    existing = await db.find_scorecard(
        DB_PATH,
        t["id"],
        player_discord_id=body.player_discord_id,
        tee_time_id=tt["id"],
        round_number=body.round_number,
    )
    if existing is not None and existing["status"] != "in_progress":
        if is_crew is None:
            is_crew = await fetch_crew_status(user["discord_id"])
        if is_crew is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Could not verify crew status with Discord — try again shortly.",
            )
        if not is_crew:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "scorecard_locked"},
            )
    # Verification rule from the bot: 2+ players in the tee time = partners
    # present -> auto-verified; solo rounds stay pending.
    player_count = await db.tee_time_player_count(DB_PATH, tt["id"])
    status_value = "verified" if player_count >= 2 else "pending"
    if body.complete:
        card_id = await db.upsert_scorecard(
            DB_PATH,
            t["id"],
            body.player_discord_id,
            None,  # team_id: the mobile scorecard flow is player-level
            tt["id"],
            body.scores,
            status_value,
            submitted_by=user["discord_id"],
            round_number=body.round_number,
            witness_name=body.witness_name,
        )
    else:
        # Live partial save: merge entered holes into the in-progress card.
        card_id = await db.save_partial_scorecard(
            DB_PATH,
            t["id"],
            body.player_discord_id,
            None,
            tt["id"],
            body.scores,
            submitted_by=user["discord_id"],
            round_number=body.round_number,
        )
    card = await db.get_scorecard(DB_PATH, card_id)
    # The Discord leaderboard board refreshes off this: the bot can't see
    # API writes, so the outbox drain picks it up (~1 min) and re-renders.
    # Partial saves enqueue too — the drain coalesces them per tournament.
    await db.enqueue_outbox(
        DB_PATH, "scorecard_submitted", {"tournament_id": t["id"]}
    )
    return {"card": _card_json(card, t.get("pars"))}


# --------------------------------------------------------------------------
# Leaderboard
# --------------------------------------------------------------------------
@app.get("/api/tournaments/{tournament_id}/leaderboard")
async def leaderboard(tournament_id: int, user: CurrentUser) -> dict:
    t = await _tournament_or_404(tournament_id)
    pars = _parse_pars(t.get("pars"))
    rounds = await db.list_rounds(DB_PATH, t["id"])
    base = {
        "tournament_id": t["id"],
        "name": t["name"],
        "format": t["format"],
        "holes": t["holes"],
        "course": t["course"],
        "status": t["status"],
        "settings": sl.format_settings(t),
        "num_rounds": len(rounds) or 1,
        "rounds": [
            {
                "round_number": r["round_number"],
                "tee_position": r["tee_position"],
                "pin_position": r["pin_position"],
                "wind_strength": r["wind_strength"],
                "start_date": r.get("start_date"),
                "end_date": r.get("end_date"),
            }
            for r in rounds
        ],
    }
    fmt = t["format"]
    if fmt == "stroke":
        # Same ranking helpers the bot uses (lr._stroke_ranked aggregates
        # verified cards across rounds; to_par is the cumulative value).
        # Live in-progress cards are included so the board moves hole by hole.
        ranked, pending = await lr._stroke_ranked(
            DB_PATH, t, include_in_progress=True)

        def _row(i: int, c: dict, verified: bool) -> dict:
            tp = c.get("to_par")
            if tp is None:
                tp = sl.to_par(c["total"], pars)
            live_rounds = [d for d in c.get("rounds", [])
                           if d.get("status") == "in_progress"]
            return {
                "position": i,
                "discord_id": c["player_discord_id"],
                "display_name": c.get("name")
                or db.display_name_of(None, c["player_discord_id"]),
                "total": c["total"],
                "to_par": tp,
                "to_par_display": sl.format_to_par(tp),
                "status": "verified" if verified else c["status"],
                "on_course": bool(c.get("on_course")),
                "thru": (max(d.get("thru", 0) for d in live_rounds)
                         if live_rounds else None),
                "rounds_played": c.get("rounds_played", 1),
                "rounds": c.get("rounds", []),
            }

        async def _pending_row(c: dict) -> dict:
            player = await db.get_player(DB_PATH, c["player_discord_id"])
            return _row(
                0,
                {**c,
                 "name": db.display_name_of(player, c["player_discord_id"])},
                False,
            )

        return {
            **base,
            "standings": [_row(i, c, True)
                          for i, c in enumerate(ranked, 1)],
            "pending": [await _pending_row(c) for c in pending],
        }
    if fmt in ("best_ball", "alt_shot", "scramble"):
        # Same ranking the bot uses (lr._team_rows aggregates per round,
        # then sums; to_par is the cumulative value). Live in-progress
        # cards are included so the board moves hole by hole.
        rows, scoreless = await lr._team_rows(
            DB_PATH, t, include_in_progress=True)
        standings = [
            {
                "position": i,
                "team_id": r["team_id"],
                "team_name": r["name"],
                "total": r["total"],
                "to_par": r["to_par"]
                if r["to_par"] is not None
                else sl.to_par(r["total"], pars),
                "to_par_display": sl.format_to_par(
                    r["to_par"]
                    if r["to_par"] is not None
                    else sl.to_par(r["total"], pars)
                ),
                "players": r["members"],
                "pending": r["pending"],
                "on_course": bool(r.get("on_course")),
                "thru": (max((d.get("thru", 0)
                              for d in r["rounds"]
                              if d.get("status") == "in_progress"),
                             default=None)),
                "rounds_played": r["rounds_played"],
                "rounds": r["rounds"],
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
def _profile_json(row: dict, is_crew: bool = False,
                  is_admin: bool = False) -> dict:
    return {
        "discord_id": row["discord_id"],
        "display_name": row["display_name"],
        "golfplus_handle": row.get("golfplus_handle"),
        "timezone": row.get("timezone"),
        "is_crew": is_crew,
        "is_admin": is_admin,
    }


@app.get("/api/players/me")
async def get_me(user: CurrentUser) -> dict:
    crew = await fetch_crew_status(user["discord_id"])
    admin = await fetch_admin_status(user["discord_id"])
    return _profile_json(user, is_crew=bool(crew), is_admin=bool(admin))


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

"""Alpenglow VGC mobile companion API.

Read-only+write JSON bridge over the Discord tournament bot's SQLite
database. Reuses the bot's own modules (``src.db``, ``src.scoring_logic``,
``src.leaderboard_render``, and pure helpers from ``src.cogs``) so the app
and Discord always agree.

This service NEVER modifies the bot's behavior: it shares the one SQLite
file, keeps transactions short (the bot's db layer opens a fresh connection
per call), and never writes to Discord (channels/messages are bot-only).
The exceptions are two Discord REST API interactions: a read-only lookup of
the caller's guild roles to gate admin-only endpoints — mirroring the bot's
own admin/mod/Tournament Director check — and granting/revoking the
Mod and Tournament Director crew roles for the in-app crew management
screen (Admin-role changes stay owner-managed in Discord itself).

Auth: every request except /api/health, /privacy, /api/auth/signup and
/api/auth/login needs ``Authorization: Bearer <token>``. The token is either
a Discord user OAuth token (validated per-request against Discord's
/users/@me endpoint and never stored or logged) or a JWT issued by the
local email+password login (``local:`` accounts, HS256, ``JWT_SECRET``).
"""

import json
import os
import re
import secrets
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import datetime as _dt
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
from src import push as push_mod  # noqa: E402
from src import score_events  # noqa: E402
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
import bcrypt  # noqa: E402
import jwt  # noqa: E402
from fastapi import (Depends, FastAPI, HTTPException, Request, Response,
                     status)  # noqa: E402
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
    """Auth dependency: local JWT first, then Discord OAuth token.

    JWTs are verified locally (no network); anything else is validated
    against Discord's /users/@me as before. Both paths return the same
    player-dict shape, keyed by players.discord_id ("local:..." for local
    accounts)."""
    auth = request.headers.get("Authorization", "")
    token = auth[len("Bearer "):].strip() if auth.startswith("Bearer ") else ""
    player_key = verify_local_jwt(token) if token else None
    if player_key:
        player = await db.get_player(DB_PATH, player_key)
        if player is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Account no longer exists",
            )
        return player
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
# Local email+password auth (alternative to Discord OAuth)
# --------------------------------------------------------------------------
# Local accounts own a synthetic identity "local:<32 hex>" stored as
# players.discord_id, so every downstream query (scorecards, season points,
# crew lists, ...) works unchanged. Auth is a JWT (HS256) sent as the same
# `Authorization: Bearer <token>` header: the auth dependency tries JWT
# verification first (no network), then falls back to Discord validation.
#
# JWT_SECRET must be set in the environment (Render dashboard). When it is
# missing, the signup/login endpoints answer 503 and JWTs are never issued
# or accepted — Discord OAuth keeps working untouched.
LOCAL_KEY_PREFIX = "local:"
JWT_EXPIRY_DAYS = 30
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _jwt_secret() -> str | None:
    """Read at call time (not import time) so tests can set/unset it."""
    return os.environ.get("JWT_SECRET") or None


def _require_jwt_secret() -> str:
    secret = _jwt_secret()
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Email login is not configured on this server yet.",
        )
    return secret


def _issue_jwt(player_key: str, email: str) -> str:
    now = int(time.time())
    payload = {
        "sub": player_key,
        "email": email,
        "type": "local",
        "iat": now,
        "exp": now + JWT_EXPIRY_DAYS * 86400,
    }
    return jwt.encode(payload, _require_jwt_secret(), algorithm="HS256")


def verify_local_jwt(token: str) -> str | None:
    """Return the player_key when token is a valid local JWT, else None."""
    secret = _jwt_secret()
    if not secret or token.count(".") != 2:
        return None
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    if payload.get("type") != "local":
        return None
    sub = payload.get("sub")
    if not isinstance(sub, str) or not sub.startswith(LOCAL_KEY_PREFIX):
        return None
    return sub


# Login brute-force protection: in-memory per-email window. Resets on
# process restart (documented limitation; the API runs as one instance).
_LOGIN_FAILS: dict[str, list[float]] = {}
_LOGIN_WINDOW_S = 10 * 60
_LOGIN_MAX_FAILS = 10


def _login_rate_limited(email: str) -> bool:
    now = time.time()
    fails = [t for t in _LOGIN_FAILS.get(email, []) if now - t < _LOGIN_WINDOW_S]
    _LOGIN_FAILS[email] = fails
    return len(fails) >= _LOGIN_MAX_FAILS


def _record_login_failure(email: str) -> None:
    _LOGIN_FAILS.setdefault(email, []).append(time.time())


class LocalSignup(BaseModel):
    email: str
    password: str
    display_name: str = ""

    @field_validator("email")
    @classmethod
    def _email_ok(cls, v: str) -> str:
        v = v.strip().lower()
        if not _EMAIL_RE.match(v):
            raise ValueError("Enter a valid email address.")
        return v

    @field_validator("password")
    @classmethod
    def _password_ok(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters.")
        return v

    @field_validator("display_name")
    @classmethod
    def _name_ok(cls, v: str) -> str:
        return v.strip()[:80]


class LocalLogin(BaseModel):
    email: str
    password: str


# NOTE: the /api/auth/* route handlers live just below `app = FastAPI(...)`
# further down this file — decorators need the app object to exist.


# --------------------------------------------------------------------------
# Crew (admin/mod) gating
# --------------------------------------------------------------------------
# Mirrors the bot's admin model (cogs/common.py is_admin): the "Tournament
# Admin" role, the "Tournament Director" role, or Manage Server permission —
# plus the "Mod" and "Admin" crew roles, since the app's admin surface is
# meant for any admin/moderator. Discord REST lookups gate crew/admin-only
# endpoints; the only write the API ever performs is granting/revoking the
# Mod and Tournament Director roles in the crew management screen (see
# discord_modify_member_role — Admin-role changes stay owner-managed in
# Discord itself).
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

    Local (email) accounts: only local admins count as crew; everyone else
    is a regular player.
    """
    if str(discord_id).startswith(LOCAL_KEY_PREFIX):
        cred = await db.get_local_credential_by_key(DB_PATH, str(discord_id))
        return bool(cred and cred["is_admin"])
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

    Local (email) accounts never touch Discord: their admin flag lives in
    local_credentials.is_admin instead.
    """
    if str(discord_id).startswith(LOCAL_KEY_PREFIX):
        cred = await db.get_local_credential_by_key(DB_PATH, str(discord_id))
        return bool(cred and cred["is_admin"])
    standing = await _fetch_guild_standing(discord_id)
    if standing is None:
        return None
    privileged, names = standing
    return privileged or bool(names & ADMIN_ROLE_NAMES)


# Mods may manage submitted AltShot scorecards alongside admins, but
# Tournament Directors may not.
MOD_ADMIN_ROLE_NAMES = frozenset({"Tournament Admin", "Admin", "Mod"})


async def fetch_mod_admin_status(discord_id: str) -> bool | None:
    """Is this Discord user a mod or admin (owner / Manage Server / Admin /
    Mod / Tournament Admin)? Tournament Directors do NOT pass.

    Used for AltShot scorecard manipulation: editing, deleting, or
    otherwise changing a submitted score.

    Local (email) accounts: local admins pass, everyone else does not.
    """
    if str(discord_id).startswith(LOCAL_KEY_PREFIX):
        cred = await db.get_local_credential_by_key(DB_PATH, str(discord_id))
        return bool(cred and cred["is_admin"])
    standing = await _fetch_guild_standing(discord_id)
    if standing is None:
        return None
    privileged, names = standing
    return privileged or bool(names & MOD_ADMIN_ROLE_NAMES)


# --------------------------------------------------------------------------
# Discord REST helpers for the in-app crew management screen.
# Module-level functions so unit tests can monkeypatch them, mirroring the
# fetch_*_status pattern above. These are the API's only Discord writes:
# granting/revoking Mod and Tournament Director roles.
# --------------------------------------------------------------------------
def _discord_headers() -> dict | None:
    """Bot-token auth headers for the Discord REST API.

    None when no bot token is configured (DISCORD_TOKEN env, else the bot's
    config).
    """
    token = os.environ.get("DISCORD_TOKEN") or config.DISCORD_TOKEN
    if not token:
        return None
    return {"Authorization": f"Bot {token}"}


def _discord_guild_id() -> str:
    """Guild id for Discord REST calls (GUILD_ID env, else bot config)."""
    return os.environ.get("GUILD_ID") or str(config.GUILD_ID or "")


async def discord_guild_roles() -> list[dict] | None:
    """All roles in the guild, via Discord's REST API.

    None when Discord couldn't be reached or the API isn't configured.
    """
    headers = _discord_headers()
    guild_id = _discord_guild_id()
    if not headers or not guild_id:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"https://discord.com/api/v10/guilds/{guild_id}/roles",
                headers=headers,
            )
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None
    try:
        return resp.json()
    except (ValueError, TypeError):
        return None


async def discord_guild_members() -> list[dict] | None:
    """Up to 1000 guild members, via Discord's REST API.

    None when Discord couldn't be reached or the API isn't configured.
    """
    headers = _discord_headers()
    guild_id = _discord_guild_id()
    if not headers or not guild_id:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"https://discord.com/api/v10/guilds/{guild_id}/members",
                params={"limit": 1000},
                headers=headers,
            )
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None
    try:
        return resp.json()
    except (ValueError, TypeError):
        return None


async def discord_modify_member_role(discord_id: str, role_id: str,
                                     action: str) -> int | None:
    """Grant (PUT) or revoke (DELETE) a guild role for a member.

    Returns the HTTP status code, or None on transport failure. Discord
    answers 204 on success and 404 when the member isn't in the guild.
    """
    headers = _discord_headers()
    guild_id = _discord_guild_id()
    if not headers or not guild_id or action not in ("grant", "revoke"):
        return None
    method = "PUT" if action == "grant" else "DELETE"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.request(
                method,
                f"https://discord.com/api/v10/guilds/{guild_id}"
                f"/members/{discord_id}/roles/{role_id}",
                headers=headers,
            )
    except httpx.HTTPError:
        return None
    return resp.status_code


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
    import asyncio

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

    # Push-notification engine: drain the push outbox and fire
    # tournament/round-start notifications once a minute.
    async def _push_loop():
        while True:
            try:
                await push_mod.check_starts(DB_PATH)
                await push_mod.drain_push_outbox(DB_PATH)
            except Exception:
                logging.getLogger("vgc.push").exception(
                    "push background loop error")
            await asyncio.sleep(60)

    task = asyncio.create_task(_push_loop())
    try:
        yield
    finally:
        task.cancel()


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


# --------------------------------------------------------------------------
# Local email+password auth routes (helpers/models defined near the top)
# --------------------------------------------------------------------------
@app.post("/api/auth/signup", status_code=status.HTTP_201_CREATED)
async def local_signup(body: LocalSignup) -> dict:
    """Create an email+password account. Returns a JWT on success."""
    _require_jwt_secret()  # 503 when unconfigured
    existing = await db.get_local_credential_by_email(DB_PATH, body.email)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "email_taken"},
        )
    name = body.display_name or body.email.split("@")[0][:80] or "Player"
    player_key = LOCAL_KEY_PREFIX + secrets.token_hex(16)
    pw_hash = bcrypt.hashpw(
        body.password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    await db.create_local_user(DB_PATH, body.email, pw_hash, player_key, name)
    return {
        "token": _issue_jwt(player_key, body.email),
        "player": {
            "discord_id": player_key,
            "display_name": name,
            "email": body.email,
        },
    }


@app.post("/api/auth/login")
async def local_login(body: LocalLogin) -> dict:
    """Email+password login. Returns a JWT on success."""
    _require_jwt_secret()  # 503 when unconfigured
    email = body.email.strip().lower()
    if _login_rate_limited(email):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed attempts — try again in a few minutes.",
        )
    cred = await db.get_local_credential_by_email(DB_PATH, email)
    # Always run a bcrypt check (dummy hash when unknown) so unknown and
    # wrong-password emails take the same time.
    dummy = bcrypt.hashpw(b"alpenglow-dummy", bcrypt.gensalt())
    stored = cred["password_hash"].encode("utf-8") if cred else dummy
    ok = bcrypt.checkpw(body.password.encode("utf-8"), stored) and cred
    if not ok:
        _record_login_failure(email)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )
    _LOGIN_FAILS.pop(email, None)
    player = await db.get_player(DB_PATH, cred["player_key"])
    return {
        "token": _issue_jwt(cred["player_key"], email),
        "player": {
            "discord_id": cred["player_key"],
            "display_name": (player or {}).get("display_name") or email,
            "email": email,
        },
    }


@app.get("/api/health")
async def health() -> dict:
    return {"ok": True}


# --------------------------------------------------------------------------
# Privacy policy (public page; the app links it from Settings)
# --------------------------------------------------------------------------
PRIVACY_POLICY_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Alpenglow VGC Privacy Policy</title>
<style>
  body { font-family: -apple-system, system-ui, sans-serif; max-width: 40em;
         margin: 2em auto; padding: 0 1.2em; line-height: 1.6; color: #222; }
  h1 { font-size: 1.5em; } h2 { font-size: 1.15em; margin-top: 1.8em; }
  .meta { color: #666; font-size: 0.9em; }
</style>
</head>
<body>
<h1>Alpenglow VGC Privacy Policy</h1>
<p class="meta">Dated 2026-10-02</p>

<p>Alpenglow VGC is a community-run golf club for Golf+ players. This policy
describes what data the Alpenglow VGC companion app and its server collect,
how it is used, and how you can delete it.</p>

<h2>What we collect</h2>
<ul>
  <li><strong>Discord identity.</strong> When you sign in with Discord, we
  receive your Discord user ID and username. Your sign-in token is validated
  per request and is never stored.</li>
  <li><strong>Email accounts.</strong> If you create an account with an email
  address instead of Discord, we store your email address and a bcrypt hash of
  your password. We never store your actual password, and we never sell or
  share your email address.</li>
  <li><strong>Player profile.</strong> Your display name, optional Golf+ handle,
  and optional timezone (used to show tee times in your local time).</li>
  <li><strong>Push notifications.</strong> Your device's APNs push token and
  your notification preferences (which event types you want to hear about).</li>
  <li><strong>Tournament activity.</strong> Tournament registrations, tee time
  memberships, and scorecards you submit — including an optional witness name
  you may enter on a scorecard. Completed rounds feed season points, course
  records, and leaderboards.</li>
  <li><strong>Match play, alt-shot, and casual rounds.</strong> Your
  participation, scores, and win/loss records in match-play, alt-shot, and
  casual rounds.</li>
</ul>

<h2>How it is used</h2>
<p>Your data is stored in the app's server database (hosted on Render) and is
used only to operate the community's tournaments, tee times, scorecards, and
leaderboards. Tournament information you participate in is visible to other
community members — for example on leaderboards, the tee sheet, and in posts
the club's Discord bot makes to the community's Discord server. Your data is
never sold, rented, or shared with advertisers.</p>

<h2>Deletion</h2>
<p>You can permanently delete your account and all associated personal data at
any time: in the app, go to <strong>Settings &rarr; Delete Account</strong>.
Alternatively, ask a server admin through the Alpenglow VGC Discord server and
they will delete it for you. Deletion removes your profile, device tokens,
notification preferences, registrations, tee time memberships, scorecards,
and season points. Shared community history (such as completed tournament
results and leaderboards) is left intact so other members' records stay
complete.</p>

<h2>Contact</h2>
<p>Questions about this policy? Reach a server admin through the Alpenglow VGC
Discord server.</p>
</body>
</html>"""


@app.get("/privacy", include_in_schema=False)
async def privacy_policy() -> Response:
    """Public static privacy policy page, linked from the app's Settings."""
    return Response(content=PRIVACY_POLICY_HTML, media_type="text/html")


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
    # Auto-link the new tournament to every active season so its season
    # points land automatically on completion. Failure-safe: a link failure
    # must never break tournament creation (the bot's /tournament create
    # does the same).
    try:
        for s in await db.get_active_seasons(DB_PATH, GUILD_ID):
            await db.add_tournament_to_season(DB_PATH, s["id"], tid)
    except Exception as e:
        print(f"season auto-link failed for tournament {tid}: {e}")
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


# --------------------------------------------------------------------------
# Crew management (admins only): list players with crew roles, grant/revoke
# Mod and Tournament Director. This is the API's only write path to Discord.
# --------------------------------------------------------------------------
@app.get("/api/admin/players")
async def admin_list_players(user: AdminUser) -> dict:
    """Registered players with their crew-relevant Discord roles.

    Resolves roles with one guild-roles fetch and one guild-members fetch
    (not one call per player). 503 when Discord can't be reached.
    """
    players = await db.list_players(DB_PATH)
    roles = await discord_guild_roles()
    members = await discord_guild_members()
    if roles is None or members is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not reach Discord — try again shortly.",
        )
    role_names = {r.get("id"): r.get("name") for r in roles if r.get("id")}
    member_crew_roles: dict[str, list[str]] = {}
    for m in members:
        uid = str((m.get("user") or {}).get("id"))
        names = sorted(
            {role_names[r] for r in (m.get("roles") or [])
             if r in role_names and role_names[r] in CREW_ROLE_NAMES}
        )
        if uid:
            member_crew_roles[uid] = names
    # Local (email) accounts resolve roles from the DB, not Discord.
    local_by_key = {
        c["player_key"]: c for c in await db.list_local_credentials(DB_PATH)
    }
    players_out = []
    for p in players:
        pid = str(p["discord_id"])
        local = local_by_key.get(pid)
        if local is not None:
            role_list = ["Admin"] if local["is_admin"] else []
        else:
            role_list = member_crew_roles.get(pid, [])
        players_out.append({
            "discord_id": pid,
            "display_name": p["display_name"],
            "golfplus_handle": p["golfplus_handle"],
            "roles": role_list,
            "is_local": local is not None,
            "email": local["email"] if local else None,
        })
    return {"players": players_out}


class CrewRoleChange(BaseModel):
    discord_id: str
    role: str
    action: str


# Roles the app may grant/revoke. "Admin" and "Tournament Admin" are
# deliberately excluded: admin changes stay owner-managed in Discord itself,
# so this endpoint can never be used to escalate someone to full admin.
MANAGEABLE_CREW_ROLES = frozenset({"Mod", "Tournament Director"})


@app.post("/api/admin/crew/roles")
async def change_crew_role(body: CrewRoleChange, user: AdminUser) -> dict:
    """Grant or revoke a crew role via the Discord REST API (admins only)."""
    if body.role not in MANAGEABLE_CREW_ROLES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="role must be exactly 'Mod' or 'Tournament Director'."
                   " 'Admin' and 'Tournament Admin' are owner-managed in"
                   " Discord and cannot be changed here.",
        )
    if body.action not in ("grant", "revoke"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="action must be 'grant' or 'revoke'.",
        )
    player = await db.get_player(DB_PATH, body.discord_id)
    if player is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Player is not registered.",
        )
    roles = await discord_guild_roles()
    if roles is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not reach Discord — try again shortly.",
        )
    role_id = next(
        (r.get("id") for r in roles if r.get("name") == body.role), None)
    if role_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Role '{body.role}' not found in the guild.",
        )
    result = await discord_modify_member_role(
        body.discord_id, role_id, body.action)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Discord request failed — try again shortly.",
        )
    if result == 204:
        return {"ok": True, "discord_id": body.discord_id,
                "role": body.role, "action": body.action}
    if result == 404:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member is not in the guild.",
        )
    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=f"Discord returned status {result}.",
    )


def _require_local_key(player_key: str) -> str:
    """Local (email) accounts only — Discord users are managed in Discord."""
    if not player_key.startswith(LOCAL_KEY_PREFIX):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Only local (email) accounts can be managed here.",
        )
    return player_key


class LocalAdminChange(BaseModel):
    is_admin: bool


@app.post("/api/admin/users/{player_key}/admin")
async def set_local_user_admin(player_key: str, body: LocalAdminChange,
                               user: AdminUser) -> dict:
    """Grant or revoke the admin flag on a local (email) account."""
    _require_local_key(player_key)
    cred = await db.get_local_credential_by_key(DB_PATH, player_key)
    if cred is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Unknown local account.",
        )
    await db.set_local_admin(DB_PATH, player_key, body.is_admin)
    return {"ok": True, "player_key": player_key,
            "is_admin": body.is_admin}


@app.post("/api/admin/users/{player_key}/reset-password")
async def reset_local_user_password(player_key: str,
                                    user: AdminUser) -> dict:
    """Generate a one-time temporary password for a local account.

    Returned exactly once — the admin reads it to the user out of band.
    """
    _require_local_key(player_key)
    cred = await db.get_local_credential_by_key(DB_PATH, player_key)
    if cred is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Unknown local account.",
        )
    temp = secrets.token_urlsafe(9)  # 12 chars
    pw_hash = bcrypt.hashpw(
        temp.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    await db.set_local_password_hash(DB_PATH, player_key, pw_hash)
    return {"ok": True, "player_key": player_key, "temp_password": temp}


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
    # Notable scoring events (ace / albatross / top-3 movement) queue
    # pushes; never allowed to break the save itself.
    await score_events.detect_score_events(
        DB_PATH, t["id"], body.player_discord_id, existing, card
    )
    # The Discord leaderboard board refreshes off this: the bot can't see
    # API writes, so the outbox drain picks it up (~1 min) and re-renders.
    # Partial saves enqueue too — the drain coalesces them per tournament.
    await db.enqueue_outbox(
        DB_PATH, "scorecard_submitted", {"tournament_id": t["id"]}
    )
    return {"card": _card_json(card, t.get("pars"))}


# --------------------------------------------------------------------------
# Casual tee times — ad-hoc rounds outside tournaments. No membership
# limits: a player may join any number of casual tee times.
# --------------------------------------------------------------------------
class CasualTeeTimeCreate(BaseModel):
    label: str
    course: str
    tee_position: str = "middle"
    pin_position: str = "white"
    wind_strength: str = "moderate"
    green_speed: str = "pro"
    starts_at: str = ""  # ISO-8601
    max_players: int = 4
    notes: str = ""

    @field_validator("label", "course")
    @classmethod
    def _nonempty(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("must not be empty")
        return v

    @field_validator("tee_position")
    @classmethod
    def _tee(cls, v: str) -> str:
        v = (v or "").strip().lower()
        if v not in ("front", "middle", "back"):
            raise ValueError(f"Unknown tee position '{v}'")
        return v

    @field_validator("pin_position")
    @classmethod
    def _pin(cls, v: str) -> str:
        v = (v or "").strip().lower()
        if v not in ("black", "white", "red"):
            raise ValueError(f"Unknown pin position '{v}'")
        return v

    @field_validator("wind_strength")
    @classmethod
    def _wind(cls, v: str) -> str:
        v = (v or "").strip().lower()
        if v not in ("low", "moderate", "severe"):
            raise ValueError(f"Unknown wind strength '{v}'")
        return v

    @field_validator("green_speed")
    @classmethod
    def _green(cls, v: str) -> str:
        v = (v or "").strip().lower()
        if v not in ("veryfast", "pro"):
            raise ValueError(f"Unknown green speed '{v}'")
        return v

    @field_validator("max_players")
    @classmethod
    def _cap(cls, v: int) -> int:
        if not 1 <= v <= 8:
            raise ValueError("max_players must be 1-8")
        return v


class CasualTeeTimeUpdate(BaseModel):
    label: str | None = None
    course: str | None = None
    tee_position: str | None = None
    pin_position: str | None = None
    wind_strength: str | None = None
    green_speed: str | None = None
    starts_at: str | None = None
    max_players: int | None = None
    notes: str | None = None


async def _casual_json(db_path, tt: dict) -> dict:
    players = []
    for pid in tt.get("players", []):
        p = await db.get_player(db_path, pid)
        players.append({
            "discord_id": pid,
            "display_name": db.display_name_of(p, pid),
        })
    return {
        "id": tt["id"],
        "creator_discord_id": tt["creator_discord_id"],
        "label": tt["label"],
        "course": tt["course"],
        "pars": tt["pars"],
        "tee_position": tt["tee_position"],
        "pin_position": tt["pin_position"],
        "wind_strength": tt["wind_strength"],
        "green_speed": tt["green_speed"],
        "starts_at": tt["starts_at"],
        "max_players": tt["max_players"],
        "notes": tt["notes"],
        "created_at": tt["created_at"],
        "players": players,
    }


async def _casual_or_404(tt_id: str) -> dict:
    tt = await db.get_casual_tee_time(DB_PATH, tt_id)
    if tt is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Casual tee time not found.")
    return tt


@app.get("/api/casual-tee-times")
async def list_casual_tee_times(user: CurrentUser) -> dict:
    tts = await db.list_casual_tee_times(DB_PATH)
    return {"tee_times": [await _casual_json(DB_PATH, tt) for tt in tts]}


@app.post("/api/casual-tee-times")
async def create_casual_tee_time(body: CasualTeeTimeCreate,
                                user: CurrentUser) -> dict:
    # Pars auto-fill from the course database, like tournaments.
    auto = gc.course_pars(body.course.strip(), 18)
    if not auto:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Unknown course — pick one from the course list.",
        )
    pars = ",".join(str(x) for x in auto)
    tt_id = await db.create_casual_tee_time(
        DB_PATH, user["discord_id"], body.label.strip(), body.course.strip(),
        pars, tee_position=body.tee_position, pin_position=body.pin_position,
        wind_strength=body.wind_strength, green_speed=body.green_speed,
        starts_at=body.starts_at.strip(), max_players=body.max_players,
        notes=body.notes.strip(),
    )
    tt = await _casual_or_404(tt_id)
    return await _casual_json(DB_PATH, tt)


@app.get("/api/casual-tee-times/{tt_id}")
async def get_casual_tee_time(tt_id: str, user: CurrentUser) -> dict:
    return await _casual_json(DB_PATH, await _casual_or_404(tt_id))


@app.patch("/api/casual-tee-times/{tt_id}")
async def update_casual_tee_time(tt_id: str, body: CasualTeeTimeUpdate,
                                user: CurrentUser) -> dict:
    tt = await _casual_or_404(tt_id)
    crew = await fetch_crew_status(user["discord_id"])
    if tt["creator_discord_id"] != user["discord_id"] and not crew:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Only the creator or crew can edit this.")
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if "course" in fields:
        auto = gc.course_pars(fields["course"].strip(), 18)
        if not auto:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Unknown course — pick one from the course list.")
        fields["pars"] = ",".join(str(x) for x in auto)
    await db.update_casual_tee_time(DB_PATH, tt_id, fields)
    return await _casual_json(DB_PATH, await _casual_or_404(tt_id))


@app.delete("/api/casual-tee-times/{tt_id}")
async def delete_casual_tee_time(tt_id: str, user: CurrentUser) -> dict:
    tt = await _casual_or_404(tt_id)
    crew = await fetch_crew_status(user["discord_id"])
    if tt["creator_discord_id"] != user["discord_id"] and not crew:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Only the creator or crew can delete this.")
    await db.delete_casual_tee_time(DB_PATH, tt_id)
    return {"ok": True}


@app.post("/api/casual-tee-times/{tt_id}/join")
async def join_casual_tee_time(tt_id: str, user: CurrentUser) -> dict:
    ok, reason = await db.join_casual_tee_time(DB_PATH, tt_id,
                                               user["discord_id"])
    if not ok and reason == "not_found":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Casual tee time not found.")
    if not ok and reason == "full":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="This tee time is full.")
    return await _casual_json(DB_PATH, await _casual_or_404(tt_id))


@app.post("/api/casual-tee-times/{tt_id}/leave")
async def leave_casual_tee_time(tt_id: str, user: CurrentUser) -> dict:
    await _casual_or_404(tt_id)
    await db.leave_casual_tee_time(DB_PATH, tt_id, user["discord_id"])
    return await _casual_json(DB_PATH, await _casual_or_404(tt_id))


# --------------------------------------------------------------------------
# Match-play records
# --------------------------------------------------------------------------
class MatchPlayTeeTimeCreate(BaseModel):
    label: str = ""
    course: str = ""
    tee_position: str = "back"
    pin_position: str = "black"
    wind_strength: str = "moderate"
    green_speed: str = "pro"
    starts_at: str = Field(default="", validate_default=True)
    format: str = "single"
    team_size: int | None = None
    notes: str = ""
    # No team names in matchplay: sides are identified by their players.

    @field_validator("label", "course")
    @classmethod
    def _nonempty(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("must not be empty")
        return v

    @field_validator("tee_position")
    @classmethod
    def _tee(cls, v: str) -> str:
        v = (v or "").strip().lower()
        if v not in ("front", "middle", "back"):
            raise ValueError(f"Unknown tee position '{v}'")
        return v

    @field_validator("pin_position")
    @classmethod
    def _pin(cls, v: str) -> str:
        v = (v or "").strip().lower()
        if v not in ("black", "white", "red"):
            raise ValueError(f"Unknown pin position '{v}'")
        return v

    @field_validator("wind_strength")
    @classmethod
    def _wind(cls, v: str) -> str:
        v = (v or "").strip().lower()
        if v not in ("low", "moderate", "severe"):
            raise ValueError(f"Unknown wind strength '{v}'")
        return v

    @field_validator("green_speed")
    @classmethod
    def _green(cls, v: str) -> str:
        v = (v or "").strip().lower()
        if v not in ("veryfast", "pro"):
            raise ValueError(f"Unknown green speed '{v}'")
        return v

    @field_validator("format")
    @classmethod
    def _format(cls, v: str) -> str:
        v = (v or "").strip().lower()
        if v not in ("single", "bestball"):
            raise ValueError("format must be 'single' or 'bestball'")
        return v

    @model_validator(mode="after")
    def _size(self):
        if self.format == "single":
            if self.team_size not in (None, 1):
                raise ValueError(
                    "team_size must be 1 (or omitted) for single match play")
            self.team_size = 1
        elif self.team_size not in (2, 3, 4):
            raise ValueError(
                "team_size (2-4) is required for best-ball match play")
        return self

    @field_validator("starts_at")
    @classmethod
    def _starts_at_required(cls, v: str) -> str:
        if not (v or "").strip():
            raise ValueError(
                "starts_at is required — pick a start date & time.")
        return v


class MatchPlayTeeTimeUpdate(BaseModel):
    label: str | None = None
    course: str | None = None
    tee_position: str | None = None
    pin_position: str | None = None
    wind_strength: str | None = None
    green_speed: str | None = None
    starts_at: str | None = None
    notes: str | None = None
    # No team names in matchplay: sides are identified by their players.

    @field_validator("starts_at")
    @classmethod
    def _starts_at_not_blank(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError(
                "starts_at cannot be cleared — pick a start date & time.")
        return v

    @field_validator("tee_position")
    @classmethod
    def _tee(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip().lower()
        if v not in ("front", "middle", "back"):
            raise ValueError(f"Unknown tee position '{v}'")
        return v

    @field_validator("pin_position")
    @classmethod
    def _pin(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip().lower()
        if v not in ("black", "white", "red"):
            raise ValueError(f"Unknown pin position '{v}'")
        return v

    @field_validator("wind_strength")
    @classmethod
    def _wind(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip().lower()
        if v not in ("low", "moderate", "severe"):
            raise ValueError(f"Unknown wind strength '{v}'")
        return v

    @field_validator("green_speed")
    @classmethod
    def _green(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip().lower()
        if v not in ("veryfast", "pro"):
            raise ValueError(f"Unknown green speed '{v}'")
        return v


class MatchPlayJoin(BaseModel):
    side_number: int = 1

    @field_validator("side_number")
    @classmethod
    def _side(cls, v: int) -> int:
        if v not in (1, 2):
            raise ValueError("side_number must be 1 or 2")
        return v


class MatchPlayScoreSave(BaseModel):
    hole_results: list[int | None] = []


# --------------------------------------------------------------------------
# Alt-shot records
# --------------------------------------------------------------------------
class AltShotTeeTimeCreate(BaseModel):
    label: str = ""
    course: str = ""
    tee_position: str = "middle"
    pin_position: str = "white"
    wind_strength: str = "moderate"
    green_speed: str = "pro"
    starts_at: str = Field(default="", validate_default=True)
    max_teams: int = 2
    team_size: int = 2
    notes: str = ""
    # No team names in alt-shot: teams are identified by their players.

    @field_validator("label", "course")
    @classmethod
    def _nonempty(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("must not be empty")
        return v

    @field_validator("tee_position")
    @classmethod
    def _tee(cls, v: str) -> str:
        v = (v or "").strip().lower()
        if v not in ("front", "middle", "back"):
            raise ValueError(f"Unknown tee position '{v}'")
        return v

    @field_validator("pin_position")
    @classmethod
    def _pin(cls, v: str) -> str:
        v = (v or "").strip().lower()
        if v not in ("black", "white", "red"):
            raise ValueError(f"Unknown pin position '{v}'")
        return v

    @field_validator("wind_strength")
    @classmethod
    def _wind(cls, v: str) -> str:
        v = (v or "").strip().lower()
        if v not in ("low", "moderate", "severe"):
            raise ValueError(f"Unknown wind strength '{v}'")
        return v

    @field_validator("green_speed")
    @classmethod
    def _green(cls, v: str) -> str:
        v = (v or "").strip().lower()
        if v not in ("veryfast", "pro"):
            raise ValueError(f"Unknown green speed '{v}'")
        return v

    @field_validator("max_teams")
    @classmethod
    def _teams(cls, v: int) -> int:
        if v not in (1, 2):
            raise ValueError("max_teams must be 1 or 2")
        return v

    @field_validator("team_size")
    @classmethod
    def _size(cls, v: int) -> int:
        if v not in (2, 3, 4):
            raise ValueError("team_size must be 2, 3, or 4")
        return v

    @field_validator("starts_at")
    @classmethod
    def _starts_at_required(cls, v: str) -> str:
        if not (v or "").strip():
            raise ValueError(
                "starts_at is required — pick a start date & time.")
        return v


class AltShotTeeTimeUpdate(BaseModel):
    label: str | None = None
    course: str | None = None
    tee_position: str | None = None
    pin_position: str | None = None
    wind_strength: str | None = None
    green_speed: str | None = None
    starts_at: str | None = None
    max_teams: int | None = None
    team_size: int | None = None
    notes: str | None = None

    @field_validator("starts_at")
    @classmethod
    def _starts_at_not_blank(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError(
                "starts_at cannot be cleared — pick a start date & time.")
        return v

    @field_validator("tee_position")
    @classmethod
    def _tee(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip().lower()
        if v not in ("front", "middle", "back"):
            raise ValueError(f"Unknown tee position '{v}'")
        return v

    @field_validator("pin_position")
    @classmethod
    def _pin(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip().lower()
        if v not in ("black", "white", "red"):
            raise ValueError(f"Unknown pin position '{v}'")
        return v

    @field_validator("wind_strength")
    @classmethod
    def _wind(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip().lower()
        if v not in ("low", "moderate", "severe"):
            raise ValueError(f"Unknown wind strength '{v}'")
        return v

    @field_validator("green_speed")
    @classmethod
    def _green(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip().lower()
        if v not in ("veryfast", "pro"):
            raise ValueError(f"Unknown green speed '{v}'")
        return v

    @field_validator("max_teams")
    @classmethod
    def _teams(cls, v: int | None) -> int | None:
        if v is not None and v not in (1, 2):
            raise ValueError("max_teams must be 1 or 2")
        return v

    @field_validator("team_size")
    @classmethod
    def _size(cls, v: int | None) -> int | None:
        if v is not None and v not in (2, 3, 4):
            raise ValueError("team_size must be 2, 3, or 4")
        return v


class AltShotJoin(BaseModel):
    extra_names: list[str] = []
    team_id: str | None = None
    # No team names in alt-shot: teams are identified by their players.


class AltShotSwitch(BaseModel):
    team_id: str


class AltShotTeamUpdate(BaseModel):
    extra_names: list[str] | None = None
    move_discord_id: str | None = None
    remove_discord_id: str | None = None
    # No team names in alt-shot: teams are identified by their players.


class AltShotScoreSubmit(BaseModel):
    holes: list[int] = []


async def _altshot_or_404(tt_id: str) -> dict:
    tt = await db.get_altshot_tee_time(DB_PATH, tt_id)
    if tt is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Alt-shot tee time not found.")
    return tt


async def _altshot_team_or_404(tt_id: str, team_id: str) -> dict:
    team = await db.get_altshot_team(DB_PATH, team_id)
    if team is None or team["tee_time_id"] != tt_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Team not found.")
    return team


async def _altshot_team_guard(team: dict, user: CurrentUser) -> None:
    """Any roster member or crew may edit the team / its score."""
    member_ids = await db.altshot_team_member_ids(DB_PATH, team["id"])
    if user["discord_id"] in member_ids:
        return
    crew = await fetch_crew_status(user["discord_id"])
    if not crew:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Only a team member or crew can do this.")


@app.get("/api/altshot-tee-times")
async def list_altshot_tee_times(user: CurrentUser) -> dict:
    tts = await db.list_altshot_tee_times(DB_PATH)
    return {"tee_times": tts}


@app.post("/api/altshot-tee-times")
async def create_altshot_tee_time(body: AltShotTeeTimeCreate,
                                  user: CurrentUser) -> dict:
    auto = gc.course_pars(body.course.strip(), 18)
    if not auto:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Unknown course — pick one from the course list.",
        )
    pars = ",".join(str(x) for x in auto)
    try:
        tt_id = await db.create_altshot_tee_time(
            DB_PATH, user["discord_id"], body.label.strip(),
            body.course.strip(), pars, tee_position=body.tee_position,
            pin_position=body.pin_position, wind_strength=body.wind_strength,
            green_speed=body.green_speed, starts_at=body.starts_at.strip(),
            max_teams=body.max_teams, team_size=body.team_size,
            notes=body.notes.strip(),
        )
    except db.AltShotError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="team_size must be 2, 3, or 4.")
    return await _altshot_or_404(tt_id)


@app.get("/api/altshot-tee-times/{tt_id}")
async def get_altshot_tee_time(tt_id: str, user: CurrentUser) -> dict:
    return await _altshot_or_404(tt_id)


@app.patch("/api/altshot-tee-times/{tt_id}")
async def update_altshot_tee_time(tt_id: str, body: AltShotTeeTimeUpdate,
                                  user: CurrentUser) -> dict:
    tt = await _altshot_or_404(tt_id)
    crew = await fetch_crew_status(user["discord_id"])
    if tt["creator_discord_id"] != user["discord_id"] and not crew:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Only the creator or crew can edit this.")
    fields = {k: v for k, v in body.model_dump(exclude_unset=True).items()
              if v is not None or k == "team_size"}
    if "course" in fields:
        auto = gc.course_pars(fields["course"].strip(), 18)
        if not auto:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Unknown course — pick one from the course list.")
        fields["pars"] = ",".join(str(x) for x in auto)
    try:
        return await db.update_altshot_tee_time(DB_PATH, tt_id, fields)
    except db.AltShotError as e:
        msg = str(e)
        if msg == "bad_team_size":
            detail = "team_size must be 2, 3, or 4."
        elif msg == "too_many":
            detail = ("A team already has more players than that roster"
                      " size.")
        else:
            detail = "Could not update this tee time."
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail)


@app.delete("/api/altshot-tee-times/{tt_id}")
async def delete_altshot_tee_time(tt_id: str, user: CurrentUser) -> dict:
    tt = await _altshot_or_404(tt_id)
    crew = await fetch_crew_status(user["discord_id"])
    if tt["creator_discord_id"] != user["discord_id"] and not crew:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Only the creator or crew can delete this.")
    await db.delete_altshot_tee_time(DB_PATH, tt_id)
    return {"ok": True}


@app.post("/api/altshot-tee-times/{tt_id}/join")
async def join_altshot_tee_time(tt_id: str, body: AltShotJoin,
                                user: CurrentUser) -> dict:
    tt = await _altshot_or_404(tt_id)
    try:
        return await db.join_altshot_tee_time(
            DB_PATH, tt_id, user["discord_id"],
            extra_names=body.extra_names,
            team_id=body.team_id)
    except db.AltShotError as e:
        msg = str(e)
        if msg == "not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                detail="Alt-shot tee time not found.")
        if msg == "no_guests":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Only registered players can play 2-team alt-shot"
                       " rounds — no typed-in names.")
        if msg == "need_team":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Pick which team to play for.")
        detail = ("This team is full." if tt.get("team_size")
                  else "This tee time already has 2 teams.")
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail=detail)


@app.post("/api/altshot-tee-times/{tt_id}/switch-team")
async def switch_altshot_team(tt_id: str, body: AltShotSwitch,
                              user: CurrentUser) -> dict:
    """Move to the other team of a 2-team tee time (before scoring)."""
    await _altshot_or_404(tt_id)
    try:
        return await db.switch_altshot_team(
            DB_PATH, tt_id, user["discord_id"], body.team_id)
    except db.AltShotError as e:
        msg = str(e)
        if msg == "not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                detail="Alt-shot tee time or team not found.")
        if msg == "teams_locked":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Teams are locked once scoring has started.")
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="That team is full.")


@app.post("/api/altshot-tee-times/{tt_id}/leave")
async def leave_altshot_tee_time(tt_id: str, user: CurrentUser) -> dict:
    await _altshot_or_404(tt_id)
    return await db.leave_altshot_tee_time(DB_PATH, tt_id,
                                           user["discord_id"])


@app.patch("/api/altshot-tee-times/{tt_id}/teams/{team_id}")
async def update_altshot_team(tt_id: str, team_id: str,
                              body: AltShotTeamUpdate,
                              user: CurrentUser) -> dict:
    team = await _altshot_team_or_404(tt_id, team_id)
    tt = await _altshot_or_404(tt_id)
    if tt.get("max_teams") == 2 and tt.get("team_size"):
        # Fixed two teams: only the organizer or crew may edit teams
        # (move a player, remove a player). Returns the full tee
        # time since moves affect both teams.
        crew = await fetch_crew_status(user["discord_id"])
        if tt["creator_discord_id"] != user["discord_id"] and not crew:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only the organizer or crew can edit teams.")
        extras = [(n or "").strip() for n in (body.extra_names or [])]
        if any(extras):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Only registered players can play 2-team alt-shot"
                       " rounds — no typed-in names.")
        try:
            return await db.manage_altshot_team(
                DB_PATH, tt_id, team_id,
                move_discord_id=body.move_discord_id,
                remove_discord_id=body.remove_discord_id)
        except db.AltShotError as e:
            msg = str(e)
            if msg == "not_found":
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Team not found.")
            if msg == "teams_locked":
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Teams are locked once scoring has started.")
            if msg == "not_on_team":
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="That player isn't on a team in this tee time.")
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="That team is full.")
    await _altshot_team_guard(team, user)
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        await db.update_altshot_team(DB_PATH, team_id, fields)
    except db.AltShotError as e:
        if str(e) == "too_many":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="That would put more players on the team than the"
                       " roster allows.")
        if str(e) == "no_guests":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Only registered players can play 2-team alt-shot"
                       " rounds — no typed-in names.")
        raise
    tt = await _altshot_or_404(tt_id)
    for t in tt["teams"]:
        if t["id"] == team_id:
            return t
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                        detail="Team not found.")


@app.post("/api/altshot-tee-times/{tt_id}/teams/{team_id}/score")
async def submit_altshot_score(tt_id: str, team_id: str,
                               body: AltShotScoreSubmit,
                               user: CurrentUser) -> dict:
    team = await _altshot_team_or_404(tt_id, team_id)
    existing = await db.get_altshot_score(DB_PATH, team_id)
    if existing is not None:
        # Changing a submitted scorecard is mod/admin only —
        # tournament directors and team members may not do it.
        ok = await fetch_mod_admin_status(user["discord_id"])
        if ok is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Could not verify mod/admin status —"
                       " try again shortly.")
        if not ok:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only a mod or admin can change a submitted score.")
    else:
        await _altshot_team_guard(team, user)
    try:
        return await db.submit_altshot_score(DB_PATH, team_id, body.holes,
                                             user["discord_id"])
    except db.AltShotError as e:
        msg = str(e)
        if msg == "not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                detail="Team not found.")
        if msg == "needs_players":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Add at least 2 players to the team before"
                       " submitting a record.")
        if msg == "team_not_full":
            tt = await _altshot_or_404(tt_id)
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"This team needs a full roster of"
                       f" {tt.get('team_size')} players before submitting"
                       " a record.")
        if msg == "teams_not_full":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Both teams need full rosters before either team"
                       " can submit a record.")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Enter 18 hole scores (1-20 each).")


@app.delete("/api/altshot-tee-times/{tt_id}/teams/{team_id}/score")
async def delete_altshot_score(tt_id: str, team_id: str,
                               user: CurrentUser) -> dict:
    team = await _altshot_team_or_404(tt_id, team_id)
    # Deleting a submitted scorecard is mod/admin only — tournament
    # directors and team members may not do it.
    ok = await fetch_mod_admin_status(user["discord_id"])
    if ok is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not verify mod/admin status — try again shortly.")
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only a mod or admin can delete a submitted score.")
    await db.delete_altshot_score(DB_PATH, team_id)
    return {"ok": True}


def _altshot_setup_or_422(team_size: int, tee_position: str,
                          pin_position: str, wind_strength: str,
                          green_speed: str) -> tuple[str, str, str, str]:
    """Validate/normalize alt-shot record filters shared by the record
    endpoints."""
    if team_size not in (2, 3, 4):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="team_size must be 2, 3, or 4.")
    out = []
    for name, val, ok in (
            ("tee_position", tee_position, ("front", "middle", "back")),
            ("pin_position", pin_position, ("black", "white", "red")),
            ("wind_strength", wind_strength, ("low", "moderate", "severe")),
            ("green_speed", green_speed, ("veryfast", "pro"))):
        norm = (val or "").strip().lower()
        if norm not in ok:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unknown {name} '{val}'.")
        out.append(norm)
    return tuple(out)  # type: ignore[return-value]


@app.get("/api/altshot-records")
async def altshot_records(user: CurrentUser, course: str, team_size: int,
                          tee_position: str = "back",
                          pin_position: str = "black",
                          wind_strength: str = "moderate",
                          green_speed: str = "pro") -> dict:
    tee, pin, wind, green = _altshot_setup_or_422(
        team_size, tee_position, pin_position, wind_strength, green_speed)
    records = await db.get_altshot_records(
        DB_PATH, course.strip(), team_size, tee_position=tee,
        pin_position=pin, wind_strength=wind, green_speed=green)
    return {"course": course.strip(), "team_size": team_size,
            "tee_position": tee, "pin_position": pin,
            "wind_strength": wind, "green_speed": green,
            "records": records}


@app.get("/api/altshot-records/summary")
async def altshot_records_summary(user: CurrentUser, team_size: int,
                                  tee_position: str = "back",
                                  pin_position: str = "black",
                                  wind_strength: str = "moderate",
                                  green_speed: str = "pro") -> dict:
    """One batched lookup: the best submitted alt-shot record per course
    for the given roster size and setup, keyed by course name. Used by
    the tee-time course picker."""
    tee, pin, wind, green = _altshot_setup_or_422(
        team_size, tee_position, pin_position, wind_strength, green_speed)
    records = await db.get_altshot_records_summary(
        DB_PATH, team_size, tee_position=tee, pin_position=pin,
        wind_strength=wind, green_speed=green)
    return {"team_size": team_size, "tee_position": tee,
            "pin_position": pin, "wind_strength": wind,
            "green_speed": green, "records": records}


# --------------------------------------------------------------------------
# Match-play
# --------------------------------------------------------------------------
async def _matchplay_or_404(tt_id: str) -> dict:
    tt = await db.get_matchplay_tee_time(DB_PATH, tt_id)
    if tt is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Match-play tee time not found.")
    return tt


async def _matchplay_member_guard(tt: dict, user: CurrentUser) -> None:
    """Any side member or crew may save/clear an in-progress score."""
    member_ids = await db.matchplay_member_ids(DB_PATH, tt["id"])
    if user["discord_id"] in member_ids:
        return
    crew = await fetch_crew_status(user["discord_id"])
    if not crew:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Only a match player or crew can do this.")


async def _require_mod_admin(user: CurrentUser) -> None:
    """Completed match-play scores are mod/admin only — tournament
    directors and players may not change them."""
    ok = await fetch_mod_admin_status(user["discord_id"])
    if ok is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not verify mod/admin status — try again shortly.")
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only a mod or admin can change a completed score.")


@app.get("/api/matchplay/tee-times")
async def list_matchplay_tee_times(user: CurrentUser) -> dict:
    tts = await db.list_matchplay_tee_times(DB_PATH)
    return {"tee_times": tts}


@app.post("/api/matchplay/tee-times")
async def create_matchplay_tee_time(body: MatchPlayTeeTimeCreate,
                                    user: CurrentUser) -> dict:
    auto = gc.course_pars(body.course.strip(), 18)
    if not auto:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Unknown course — pick one from the course list.",
        )
    pars = ",".join(str(x) for x in auto)
    try:
        tt_id = await db.create_matchplay_tee_time(
            DB_PATH, user["discord_id"], body.label.strip(),
            body.course.strip(), pars, tee_position=body.tee_position,
            pin_position=body.pin_position, wind_strength=body.wind_strength,
            green_speed=body.green_speed, starts_at=body.starts_at.strip(),
            format=body.format, team_size=body.team_size or 1,
            notes=body.notes.strip(),
        )
    except db.MatchPlayError as e:
        msg = str(e)
        if msg == "bad_format":
            detail = "format must be 'single' or 'bestball'."
        elif msg == "bad_team_size":
            detail = ("team_size must be 1 for single match play and 2-4"
                      " for best-ball match play.")
        else:
            detail = "Could not create this match-play tee time."
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail)
    return await _matchplay_or_404(tt_id)


@app.get("/api/matchplay/tee-times/{tt_id}")
async def get_matchplay_tee_time(tt_id: str, user: CurrentUser) -> dict:
    return await _matchplay_or_404(tt_id)


@app.patch("/api/matchplay/tee-times/{tt_id}")
async def update_matchplay_tee_time(tt_id: str, body: MatchPlayTeeTimeUpdate,
                                    user: CurrentUser) -> dict:
    tt = await _matchplay_or_404(tt_id)
    crew = await fetch_crew_status(user["discord_id"])
    if tt["creator_discord_id"] != user["discord_id"] and not crew:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Only the creator or crew can edit this.")
    fields = {k: v for k, v in body.model_dump(exclude_unset=True).items()
              if v is not None}
    if "course" in fields:
        auto = gc.course_pars(fields["course"].strip(), 18)
        if not auto:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Unknown course — pick one from the course list.")
        fields["pars"] = ",".join(str(x) for x in auto)
    return await db.update_matchplay_tee_time(DB_PATH, tt_id, fields)


@app.delete("/api/matchplay/tee-times/{tt_id}")
async def delete_matchplay_tee_time(tt_id: str, user: CurrentUser) -> dict:
    tt = await _matchplay_or_404(tt_id)
    crew = await fetch_crew_status(user["discord_id"])
    if tt["creator_discord_id"] != user["discord_id"] and not crew:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Only the creator or crew can delete this.")
    await db.delete_matchplay_tee_time(DB_PATH, tt_id)
    return {"ok": True}


@app.post("/api/matchplay/tee-times/{tt_id}/join")
async def join_matchplay_tee_time(tt_id: str, body: MatchPlayJoin,
                                  user: CurrentUser) -> dict:
    await _matchplay_or_404(tt_id)
    try:
        return await db.join_matchplay_tee_time(
            DB_PATH, tt_id, user["discord_id"], body.side_number)
    except db.MatchPlayError as e:
        msg = str(e)
        if msg == "not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                detail="Match-play tee time not found.")
        if msg == "already_in":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="You are already in this match.")
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="That side is full.")


@app.post("/api/matchplay/tee-times/{tt_id}/leave")
async def leave_matchplay_tee_time(tt_id: str, user: CurrentUser) -> dict:
    await _matchplay_or_404(tt_id)
    return await db.leave_matchplay_tee_time(DB_PATH, tt_id,
                                             user["discord_id"])


@app.get("/api/matchplay/tee-times/{tt_id}/score")
async def get_matchplay_score(tt_id: str, user: CurrentUser) -> dict:
    await _matchplay_or_404(tt_id)
    score = await db.matchplay_score_json(DB_PATH, tt_id)
    return {"score": score}


@app.put("/api/matchplay/tee-times/{tt_id}/score")
async def save_matchplay_score(tt_id: str, body: MatchPlayScoreSave,
                               user: CurrentUser) -> dict:
    tt = await _matchplay_or_404(tt_id)
    existing = await db.get_matchplay_score(DB_PATH, tt_id)
    if existing is not None and existing["status"] == "completed":
        # Changing a completed match is mod/admin only — tournament
        # directors and players may not do it.
        await _require_mod_admin(user)
    else:
        await _matchplay_member_guard(tt, user)
    try:
        score = await db.save_matchplay_score(
            DB_PATH, tt_id, body.hole_results, user["discord_id"])
    except db.MatchPlayError as e:
        msg = str(e)
        if msg == "not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                detail="Match-play tee time not found.")
        if msg == "sides_not_full":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Both sides must be full before scoring can start.")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Send 18 hole results: 1 (side 1 wins), -1 (side 2"
                   " wins), 0 (halved), or null (not played).")
    return {"score": score}


@app.delete("/api/matchplay/tee-times/{tt_id}/score")
async def delete_matchplay_score(tt_id: str, user: CurrentUser) -> dict:
    tt = await _matchplay_or_404(tt_id)
    existing = await db.get_matchplay_score(DB_PATH, tt_id)
    if existing is not None and existing["status"] == "completed":
        # Clearing a completed match is mod/admin only.
        await _require_mod_admin(user)
    else:
        await _matchplay_member_guard(tt, user)
    try:
        await db.delete_matchplay_score(DB_PATH, tt_id)
    except db.MatchPlayError as e:
        if str(e) == "not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                detail="Match-play tee time not found.")
        raise
    return {"ok": True}


@app.get("/api/matchplay/records")
async def matchplay_records(user: CurrentUser, format: str) -> dict:
    fmt = (format or "").strip().lower()
    if fmt not in ("single", "bestball"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown format '{format}'.")
    records = await db.get_matchplay_records(DB_PATH, fmt)
    return {"format": fmt, "records": records}


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
class SeasonCreate(BaseModel):
    """Mirrors /season create: admin-only, one active season at a time."""

    name: str
    start_date: str | None = None  # YYYY-MM-DD
    end_date: str | None = None  # YYYY-MM-DD

    @field_validator("name")
    @classmethod
    def _nonempty(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("must be non-empty")
        return v[:80]

    @field_validator("start_date", "end_date")
    @classmethod
    def _iso_date(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = (v or "").strip()
        if not v:
            return None
        try:
            _dt.date.fromisoformat(v)
        except ValueError:
            raise ValueError("must be a valid YYYY-MM-DD date")
        return v

    @model_validator(mode="after")
    def _date_order(self) -> "SeasonCreate":
        if self.start_date and self.end_date:
            if _dt.date.fromisoformat(self.start_date) > _dt.date.fromisoformat(
                self.end_date
            ):
                raise ValueError("start_date must not be after end_date")
        return self


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
            "start_date": season.get("start_date"),
            "end_date": season.get("end_date"),
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


@app.post("/api/seasons", status_code=status.HTTP_201_CREATED)
async def create_season(body: SeasonCreate, user: AdminUser) -> dict:
    existing = await db.get_active_season(DB_PATH, GUILD_ID)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "active_season_exists",
                "active_season_id": existing["id"],
                "active_season_name": existing["name"],
            },
        )
    sid = await db.create_season(
        DB_PATH, GUILD_ID, body.name, user["discord_id"],
        start_date=body.start_date, end_date=body.end_date,
    )
    season = await db.get_season(DB_PATH, sid)
    return {
        "id": season["id"],
        "name": season["name"],
        "status": season["status"],
        "start_date": season.get("start_date"),
        "end_date": season.get("end_date"),
    }


@app.post("/api/seasons/{season_id}/complete")
async def complete_season(season_id: int, user: AdminUser) -> dict:
    season = await db.get_season(DB_PATH, season_id)
    if not season or str(season["guild_id"]) != str(GUILD_ID):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "season_not_found"},
        )
    if season["status"] != "active":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "season_not_active"},
        )
    await db.complete_season(DB_PATH, season_id)
    season = await db.get_season(DB_PATH, season_id)
    return {
        "id": season["id"],
        "name": season["name"],
        "status": season["status"],
        "start_date": season.get("start_date"),
        "end_date": season.get("end_date"),
    }


# --------------------------------------------------------------------------
# Player profile
# --------------------------------------------------------------------------
def _profile_json(row: dict, is_crew: bool = False,
                  is_admin: bool = False,
                  can_manage_scores: bool = False) -> dict:
    return {
        "discord_id": row["discord_id"],
        "display_name": row["display_name"],
        "golfplus_handle": row.get("golfplus_handle"),
        "timezone": row.get("timezone"),
        "is_crew": is_crew,
        "is_admin": is_admin,
        "can_manage_scores": can_manage_scores,
    }


@app.get("/api/players/me")
async def get_me(user: CurrentUser) -> dict:
    crew = await fetch_crew_status(user["discord_id"])
    admin = await fetch_admin_status(user["discord_id"])
    mod_admin = await fetch_mod_admin_status(user["discord_id"])
    return _profile_json(user, is_crew=bool(crew), is_admin=bool(admin),
                         can_manage_scores=bool(mod_admin))


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


@app.delete("/api/players/me")
async def delete_me(user: CurrentUser) -> dict:
    """Permanently delete the caller's account and personal data.

    Removes the player row, device tokens, notification preferences, and
    every registration, tee time membership, scorecard, and season-points
    row tied to the user (see db.delete_player_data for the full cascade).
    Shared tournament history is left intact. Irreversible.
    """
    await db.delete_player_data(DB_PATH, user["discord_id"])
    return {"deleted": True, "discord_id": user["discord_id"]}


# --------------------------------------------------------------------------
# Push notifications: device registration + per-user prefs
# --------------------------------------------------------------------------
class DeviceRegister(BaseModel):
    push_token: str
    platform: str = "ios"

    @field_validator("push_token")
    @classmethod
    def _token_ok(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 16:
            raise ValueError("push_token looks invalid")
        return v


@app.post("/api/devices/register")
async def register_device(body: DeviceRegister,
                          user: CurrentUser) -> dict:
    await db.register_device(DB_PATH, user["discord_id"], body.push_token,
                             body.platform)
    return {"ok": True}


@app.delete("/api/devices")
async def unregister_device(push_token: str, user: CurrentUser) -> dict:
    await db.unregister_device(DB_PATH, user["discord_id"], push_token)
    return {"ok": True}


class NotificationPrefsUpdate(BaseModel):
    tournament_starts: bool | None = None
    round_starts: bool | None = None
    ace: bool | None = None
    albatross: bool | None = None
    top3_changes: bool | None = None


@app.get("/api/notifications/prefs")
async def get_notification_prefs(user: CurrentUser) -> dict:
    return await db.get_notification_prefs(DB_PATH, user["discord_id"])


@app.put("/api/notifications/prefs")
async def put_notification_prefs(body: NotificationPrefsUpdate,
                                 user: CurrentUser) -> dict:
    current = await db.get_notification_prefs(DB_PATH, user["discord_id"])
    merged = {
        k: (current[k] if v is None else v)
        for k, v in body.model_dump().items()
    }
    return await db.set_notification_prefs(DB_PATH, user["discord_id"],
                                           merged)


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

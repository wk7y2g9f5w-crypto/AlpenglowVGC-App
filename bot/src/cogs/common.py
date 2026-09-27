"""Shared helpers for cogs: admin gating, tournament autocomplete/resolution,
and per-viewer timezone formatting."""
from datetime import datetime, timezone

import discord
from discord import app_commands
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src import config, db

ADMIN_ROLE_NAME = config.ADMIN_ROLE_NAME
DIRECTOR_ROLE_NAME = "Tournament Director"


def resolve_tz(tz_name: str | None) -> ZoneInfo:
    """ZoneInfo for an IANA name, falling back to UTC when unset/invalid."""
    if tz_name:
        try:
            return ZoneInfo(tz_name)
        except ZoneInfoNotFoundError:
            pass
    return ZoneInfo("UTC")


def format_tee_time_local(starts_at: str | None, tz_name: str | None) -> str:
    """Format an ISO UTC timestamp in the viewer's timezone.

    Returns e.g. "Sep 26, 7:30 PM MDT", or "" when unparseable.
    Pure function, unit-testable.
    """
    if not starts_at:
        return ""
    try:
        dt = datetime.fromisoformat(starts_at)
    except (ValueError, TypeError):
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone(resolve_tz(tz_name))
    time_part = local.strftime("%I:%M %p").lstrip("0")
    return f"{local.strftime('%b')} {local.day}, {time_part} {local.strftime('%Z')}".strip()


async def viewer_tee_time_when(db_path, discord_id: str,
                               starts_at: str | None) -> str:
    """' Sep 26, 7:30 PM MDT' in the viewer's own timezone (leading space
    included for autocomplete labels), or '' when unknown."""
    tz_name = await db.get_timezone(db_path, discord_id)
    when = format_tee_time_local(starts_at, tz_name)
    return f" {when}" if when else ""


async def is_admin(interaction: discord.Interaction) -> bool:
    """True if the user has the admin role, the Tournament Director role,
    or Manage Server permission.

    Creates the admin role on first use (and returns False for the caller so
    the role can be assigned before it grants anything).
    """
    member = interaction.user
    if not isinstance(member, discord.Member):
        return False
    if member.guild_permissions.manage_guild:
        return True
    role = discord.utils.get(interaction.guild.roles, name=ADMIN_ROLE_NAME)
    if role is None:
        try:
            await interaction.guild.create_role(
                name=ADMIN_ROLE_NAME,
                reason="Tournament bot admin role (auto-created)",
            )
        except discord.Forbidden:
            return False
        return False  # freshly created; nobody has it yet
    if role in member.roles:
        return True
    director = discord.utils.get(interaction.guild.roles, name=DIRECTOR_ROLE_NAME)
    return director is not None and director in member.roles


async def require_admin(interaction: discord.Interaction) -> bool:
    if await is_admin(interaction):
        return True
    await interaction.response.send_message(
        f"⛔ You need the **{ADMIN_ROLE_NAME}** or **{DIRECTOR_ROLE_NAME}** role, "
        "or the Manage Server permission to use that command.",
        ephemeral=True,
    )
    return False


def _db_path(interaction: discord.Interaction) -> str:
    return interaction.client.db_path


async def tournament_autocomplete(interaction: discord.Interaction, current: str,
                                  statuses: tuple = ("registration_open", "in_progress")):
    rows = await db.list_tournaments(
        _db_path(interaction), str(interaction.guild_id),
        statuses=list(statuses), search=current or None,
    )
    return [
        app_commands.Choice(
            name=f"{r['name']} [{r['status'].replace('_', ' ')}]"[:100],
            value=r["id"],
        )
        for r in rows[:25]
    ]


async def open_tournament_autocomplete(interaction: discord.Interaction, current: str):
    return await tournament_autocomplete(interaction, current,
                                         statuses=("registration_open",))


async def active_tournament_autocomplete(interaction: discord.Interaction, current: str):
    return await tournament_autocomplete(interaction, current,
                                         statuses=("registration_open", "in_progress"))


async def inprogress_tournament_autocomplete(interaction: discord.Interaction, current: str):
    return await tournament_autocomplete(interaction, current,
                                         statuses=("in_progress",))


async def resolve_tournament(interaction: discord.Interaction, tournament_id,
                             statuses) -> tuple[dict | None, str | None]:
    """Resolve an optional tournament id, defaulting to the single matching one.

    Returns (tournament_dict, None) or (None, error_message).
    """
    path = _db_path(interaction)
    guild_id = str(interaction.guild_id)
    if tournament_id:
        t = await db.get_tournament(path, tournament_id)
        if not t or t["guild_id"] != guild_id:
            return None, "❌ That tournament wasn't found in this server."
        if t["status"] not in statuses:
            return None, (f"❌ **{t['name']}** is {t['status'].replace('_', ' ')} — "
                           "pick a tournament with the right status.")
        return t, None
    ts = await db.list_tournaments(path, guild_id, statuses=list(statuses))
    if len(ts) == 1:
        return ts[0], None
    if not ts:
        return None, "❌ No tournament is currently available for that action."
    names = ", ".join(f"**{t['name']}**" for t in ts[:5])
    more = f" (+{len(ts) - 5} more)" if len(ts) > 5 else ""
    return None, ("❌ Multiple tournaments are running — please choose one "
                  f"with the tournament option: {names}{more}.")


def admin_role_mention(guild: discord.Guild) -> str:
    role = discord.utils.get(guild.roles, name=ADMIN_ROLE_NAME)
    return role.mention if role else "an admin"

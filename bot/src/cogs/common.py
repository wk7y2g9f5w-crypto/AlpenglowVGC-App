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


# Every role that counts as "the crew": admins, mods, tournament directors.
CREW_ROLE_NAMES = frozenset(
    {"Tournament Admin", "Admin", "Mod", "Tournament Director"}
)


async def is_crew(interaction: discord.Interaction) -> bool:
    """True if the user is crew (admin/mod/tournament director roles, or
    Manage Server / Administrator permission)."""
    member = interaction.user
    if not isinstance(member, discord.Member):
        return False
    perms = member.guild_permissions
    if perms.manage_guild or perms.administrator:
        return True
    role_names = {r.name for r in member.roles}
    return bool(role_names & CREW_ROLE_NAMES)


async def require_admin(interaction: discord.Interaction) -> bool:
    if await is_admin(interaction):
        return True
    await interaction.response.send_message(
        f"⛔ You need the **{ADMIN_ROLE_NAME}** or **{DIRECTOR_ROLE_NAME}** role, "
        "or the Manage Server permission to use that command.",
        ephemeral=True,
    )
    return False


# Roles that count as full admins for destructive actions (tournament
# delete). Mods and Tournament Directors are deliberately excluded.
STRICT_ADMIN_ROLE_NAMES = ("Tournament Admin", "Admin")


async def is_strict_admin(interaction: discord.Interaction) -> bool:
    """True for the guild owner / Manage Server holders / Admin role.

    Unlike is_admin, Tournament Directors and Mods do NOT pass.
    """
    member = interaction.user
    if not isinstance(member, discord.Member):
        return False
    perms = member.guild_permissions
    if perms.manage_guild or perms.administrator:
        return True
    role_names = {r.name for r in member.roles}
    return bool(role_names & set(STRICT_ADMIN_ROLE_NAMES))


async def require_strict_admin(interaction: discord.Interaction) -> bool:
    if await is_strict_admin(interaction):
        return True
    await interaction.response.send_message(
        "⛔ Only an **Admin** (or the server owner) can do that — "
        "mods and tournament directors can't.",
        ephemeral=True,
    )
    return False


# Channel names accepted for the tee-sheet boards and the /tee_sheet
# commands, in preference order. The emoji-prefixed forms are tried first
# (in case the guild uses them); "tee-sheet" remains as a legacy fallback.
TEESHEET_CHANNEL_NAMES: tuple[str, ...] = (
    "⛳️tee-times",
    "⛳tee-times",
    "tee-times",
    "tee-sheet",
)


def find_teesheet_channel(bot, guild_id):
    """The guild's tee-sheet channel, or None when it has none of the
    accepted names."""
    try:
        guild = bot.get_guild(int(guild_id))
    except (TypeError, ValueError):
        return None
    if guild is None:
        return None
    for name in TEESHEET_CHANNEL_NAMES:
        channel = discord.utils.get(guild.text_channels, name=name)
        if channel is not None:
            return channel
    return None


# Commands that may only be invoked from a designated channel, mapped by
# command key -> accepted channel names (first is the display name). The
# gate is strict: when the guild has no channel with any of those names,
# the command is refused with an error naming the channel (the server
# isn't set up for it) rather than running elsewhere.
DESIGNATED_CHANNELS: dict[str, tuple[str, ...]] = {
    "tee_sheet": TEESHEET_CHANNEL_NAMES,
}


async def require_designated_channel(
    interaction: discord.Interaction, command_key: str
) -> bool:
    """True when the interaction ran in the command's designated channel.

    Otherwise sends an ephemeral error naming the required channel and
    returns False, so a designated command never goes through from the
    wrong place — including a guild that lacks the channel entirely.
    """
    channel_names = DESIGNATED_CHANNELS.get(command_key)
    if channel_names is None:
        return True
    guild = interaction.guild
    target = None
    if guild is not None:
        for name in channel_names:
            target = discord.utils.get(guild.text_channels, name=name)
            if target is not None:
                break
    if target is None or interaction.channel_id != target.id:
        await interaction.response.send_message(
            f"❌ This command can only be used in #{channel_names[0]}.",
            ephemeral=True,
        )
        return False
    return True


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


async def any_tournament_autocomplete(interaction: discord.Interaction, current: str):
    return await tournament_autocomplete(
        interaction, current,
        statuses=("registration_open", "in_progress", "completed"))


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

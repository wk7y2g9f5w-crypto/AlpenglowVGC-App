"""Player profile: /link_golfplus, /unlink_golfplus, and /set_timezone.

Golf+ offers no public API, so this is a manual link: each member records
the Golf+ username they play under, and the bot shows it next to their
Discord name on the Tee Sheet, rosters, and leaderboards so people can
find each other in-game.

Timezone: tee times are entered in each player's own timezone (stored via
/set_timezone, default UTC) and saved as UTC; Discord displays them in
each viewer's local timezone automatically.
"""
import discord
from discord import app_commands
from discord.ext import commands
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src import db
from src import teesheet as ts

MAX_HANDLE_LEN = 32

COMMON_TIMEZONES = [
    ("Mountain (Denver)", "America/Denver"),
    ("Central (Chicago)", "America/Chicago"),
    ("Eastern (New York)", "America/New_York"),
    ("Pacific (Los Angeles)", "America/Los_Angeles"),
    ("Arizona (Phoenix, no DST)", "America/Phoenix"),
    ("Alaska (Anchorage)", "America/Anchorage"),
    ("Hawaii (Honolulu)", "Pacific/Honolulu"),
    ("UTC", "UTC"),
]


async def timezone_autocomplete(interaction: discord.Interaction, current: str):
    """Suggest common timezones; any valid IANA name is also accepted."""
    needle = (current or "").strip().lower()
    return [
        app_commands.Choice(name=label, value=tz)
        for label, tz in COMMON_TIMEZONES
        if not needle or needle in label.lower() or needle in tz.lower()
    ][:25]


def clean_handle(raw: str) -> str | None:
    """Normalized handle, or None when the input is unusable."""
    handle = (raw or "").strip()
    if not handle or len(handle) > MAX_HANDLE_LEN:
        return None
    return handle


class Profile(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="link_golfplus",
        description="Link your Golf+ username to your Discord account",
    )
    @app_commands.describe(handle="Your Golf+ username, exactly as it appears in-game")
    async def link_golfplus(self, interaction: discord.Interaction, handle: str):
        clean = clean_handle(handle)
        if clean is None:
            await interaction.response.send_message(
                "❌ That doesn't look like a Golf+ username — "
                f"keep it to 1–{MAX_HANDLE_LEN} characters.",
                ephemeral=True,
            )
            return
        member = interaction.user
        await db.upsert_player(self.bot.db_path, str(member.id),
                               member.display_name[:80])
        await db.set_golfplus_handle(self.bot.db_path, str(member.id), clean)
        await interaction.response.send_message(
            f"✅ Linked! You'll now show as "
            f"**{member.display_name} (Golf+: {clean})** on the Tee Sheet, "
            f"rosters, and leaderboards.",
            ephemeral=True,
        )
        await ts.maybe_refresh(self.bot, str(interaction.guild_id))

    @app_commands.command(
        name="unlink_golfplus",
        description="Remove your linked Golf+ username",
    )
    async def unlink_golfplus(self, interaction: discord.Interaction):
        await db.set_golfplus_handle(self.bot.db_path, str(interaction.user.id),
                                     None)
        await interaction.response.send_message(
            "✅ Your Golf+ username is unlinked.", ephemeral=True
        )
        await ts.maybe_refresh(self.bot, str(interaction.guild_id))


    @app_commands.command(
        name="set_timezone",
        description="Set your timezone so tee times use your local time",
    )
    @app_commands.autocomplete(timezone=timezone_autocomplete)
    @app_commands.describe(
        timezone="Pick from the list, or type any IANA zone like Europe/London"
    )
    async def set_timezone(self, interaction: discord.Interaction, timezone: str):
        tz_name = (timezone or "").strip()
        try:
            ZoneInfo(tz_name)
        except ZoneInfoNotFoundError:
            await interaction.response.send_message(
                f"❌ `{tz_name}` isn't a timezone I recognize — pick one from "
                f"the suggestions (e.g. `America/Denver`).",
                ephemeral=True,
            )
            return
        member = interaction.user
        await db.upsert_player(self.bot.db_path, str(member.id),
                               member.display_name[:80])
        await db.set_timezone(self.bot.db_path, str(member.id), tz_name)
        await interaction.response.send_message(
            f"✅ Your timezone is now **{tz_name}**. Times you enter in "
            f"`/tee_time create` will be read as your local time.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Profile(bot))

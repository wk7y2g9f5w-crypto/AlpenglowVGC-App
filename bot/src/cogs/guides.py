"""Auto-posted command guides: #command-guide (players), #crew-guide (crew).

On startup the bot posts each guide into its channel if the bot's own
guide message isn't already there (identified by a marker in the embed
footer). `/guide refresh` (admin) re-posts them on demand.
Best-effort throughout — guide posting never breaks commands.
"""
import discord
from discord import app_commands
from discord.ext import commands

from src import guide_content as gc
from src.cogs.common import require_admin

PLAYER_CHANNEL = "command-guide"
CREW_CHANNEL = "crew-guide"
PLAYER_MARKER = "Alpenglow VC player command guide"
CREW_MARKER = "Alpenglow VC crew command guide"


def build_player_embed() -> discord.Embed:
    embed = discord.Embed(
        title="📖 Player Command Guide",
        description=(
            "Everything you need to play: register, book tee times, submit "
            "scores, and check the boards.\nMost commands take a "
            "`tournament` option — if only one fits, it's picked for you."
        ),
        color=0x1B6CA8,
    )
    for cat, entries in gc.grouped(gc.PLAYER_COMMANDS):
        lines = [f"**{cmd}** — {detail}" for cmd, detail in entries]
        embed.add_field(name=cat, value="\n".join(lines), inline=False)
    embed.set_footer(text=f"{PLAYER_MARKER} • auto-posted by the tournament bot")
    return embed


def build_crew_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🛠️ Crew Command Guide",
        description=(
            "Full reference including admin-only commands. Player commands "
            "are listed first — the crew-only ones are under "
            "**Running tournaments** and **Seasons & boards**."
        ),
        color=0xF0B429,
    )
    for cat, entries in gc.grouped(gc.CREW_COMMANDS):
        lines = [f"**{cmd}** — {detail}" for cmd, detail in entries]
        embed.add_field(name=cat, value="\n".join(lines), inline=False)
    embed.set_footer(text=f"{CREW_MARKER} • auto-posted by the tournament bot")
    return embed


async def _find_guide_message(channel: discord.TextChannel,
                              marker: str,
                              bot_user_id: int) -> discord.Message | None:
    """The bot's own guide message in this channel, if present."""
    try:
        async for msg in channel.history(limit=20):
            if msg.author.id != bot_user_id or not msg.embeds:
                continue
            footer = (msg.embeds[0].footer.text or "")
            if marker in footer:
                return msg
    except (discord.Forbidden, discord.HTTPException):
        return None
    return None


async def _upsert_guide(channel: discord.TextChannel, embed: discord.Embed,
                       marker: str, bot_user_id: int) -> bool:
    """Edit the existing guide message, or post it if missing."""
    existing = await _find_guide_message(channel, marker, bot_user_id)
    try:
        if existing is not None:
            msg = await existing.edit(embed=embed)
            how = "edit"
        else:
            msg = await channel.send(embed=embed)
            how = "send"
        # TEMP DIAGNOSTIC (2026-10-07): log what the API echoed back.
        if msg.embeds:
            _rd = msg.embeds[0].to_dict()
            print(f"guide debug2: {how} #{channel.name} echo title={_rd.get('title')!r} "
                  f"fields={len(_rd.get('fields', []))} keys={sorted(_rd.keys())}")
        else:
            print(f"guide debug2: {how} #{channel.name} echo EMPTY (0 embeds)")
        return True
    except (discord.Forbidden, discord.HTTPException) as e:
        print(f"guide upsert failed in #{channel.name}: {e}")
        return False


async def startup(bot: commands.Bot, guild_id: str) -> None:
    """Post both guides on bot startup when they're missing. Never raises."""
    try:
        guild = bot.get_guild(int(guild_id))
        if guild is None or bot.user is None:
            return
        for channel_name, embed, marker in (
            (PLAYER_CHANNEL, build_player_embed(), PLAYER_MARKER),
            (CREW_CHANNEL, build_crew_embed(), CREW_MARKER),
        ):
            channel = discord.utils.get(guild.text_channels, name=channel_name)
            if channel is None:
                continue
            await _upsert_guide(channel, embed, marker, bot.user.id)
    except Exception as e:  # noqa: BLE001 - guides are best-effort
        print(f"guide startup failed for guild {guild_id}: {e}")


class Guides(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    guide = app_commands.Group(
        name="guide", description="Manage the command guide channels"
    )

    @guide.command(name="refresh", description="Re-post the command guides (admin)")
    async def guide_refresh(self, interaction: discord.Interaction):
        if not await require_admin(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        if self.bot.user is None:
            await interaction.followup.send("❌ Bot user not available.", ephemeral=True)
            return
        results = []
        for channel_name, embed, marker in (
            (PLAYER_CHANNEL, build_player_embed(), PLAYER_MARKER),
            (CREW_CHANNEL, build_crew_embed(), CREW_MARKER),
        ):
            channel = discord.utils.get(
                interaction.guild.text_channels, name=channel_name
            )
            if channel is None:
                results.append(f"❌ #{channel_name} not found in this server.")
                continue
            ok = await _upsert_guide(channel, embed, marker, self.bot.user.id)
            results.append(
                f"✅ #{channel_name} updated."
                if ok else f"⚠️ Couldn't post in #{channel_name} (check my permissions)."
            )
        await interaction.followup.send("\n".join(results), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Guides(bot))

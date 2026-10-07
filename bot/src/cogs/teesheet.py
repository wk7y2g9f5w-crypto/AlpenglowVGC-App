"""The Tee Sheet board: /tee_sheet post|refresh (admin only)."""
import discord
from discord import app_commands
from discord.ext import commands

from src import db
from src import teesheet as ts
from src.cogs.common import require_admin, require_designated_channel


class TeeSheet(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    teesheet_group = app_commands.Group(
        name="tee_sheet", description="Manage the Tee Sheet board"
    )

    @teesheet_group.command(
        name="post", description="Post the Tee Sheet boards (admin, #⛳️tee-times only)"
    )
    async def teesheet_post(self, interaction: discord.Interaction):
        if not await require_designated_channel(interaction, "tee_sheet"):
            return
        if not await require_admin(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await ts.post_boards(self.bot, str(interaction.guild_id),
                                 interaction.channel)
        except (discord.Forbidden, discord.HTTPException) as e:
            await interaction.followup.send(
                f"❌ Couldn't post the boards here: {e}", ephemeral=True
            )
            return
        await interaction.followup.send(
            "✅ Tee Sheet boards posted and pinned. They'll stay up to date "
            "automatically.",
            ephemeral=True,
        )

    @teesheet_group.command(
        name="refresh", description="Refresh the Tee Sheet boards now (admin)"
    )
    async def teesheet_refresh(self, interaction: discord.Interaction):
        if not await require_designated_channel(interaction, "tee_sheet"):
            return
        if not await require_admin(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        rec = await db.get_board(self.bot.db_path, str(interaction.guild_id),
                                 ts.REGISTER_KIND)
        if not rec:
            await interaction.followup.send(
                "No boards posted yet — run `/tee_sheet post` in #⛳️tee-times first.",
                ephemeral=True,
            )
            return
        await ts.refresh_boards(self.bot, str(interaction.guild_id))
        await interaction.followup.send("✅ Boards refreshed.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(TeeSheet(bot))

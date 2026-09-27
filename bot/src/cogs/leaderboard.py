"""/leaderboard — on-demand board plus the auto-refresh hook used by cogs."""
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from src import leaderboard_render
from src.cogs.common import (
    active_tournament_autocomplete,
    resolve_tournament,
)


class Leaderboard(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="leaderboard", description="Show the tournament leaderboard")
    @app_commands.autocomplete(tournament=active_tournament_autocomplete)
    @app_commands.describe(tournament="Defaults to the single active tournament")
    async def leaderboard(self, interaction: discord.Interaction,
                          tournament: Optional[int] = None):
        t, err = await resolve_tournament(
            interaction, tournament,
            ["registration_open", "in_progress", "completed"],
        )
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        embed = await leaderboard_render.build_leaderboard_embed(
            self.bot.db_path, t["id"], final=(t["status"] == "completed")
        )
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Leaderboard(bot))

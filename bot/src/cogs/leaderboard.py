"""The live leaderboard: /leaderboard show|post|refresh.

- show: on-demand leaderboard embed (everyone).
- post/refresh: the pinned live board in #🏁leaderboard (admin only).
  The board auto-refreshes on every score via the outbox drain.
"""
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from src import db
from src import leaderboard_render
from src.cogs.common import (
    active_tournament_autocomplete,
    require_admin,
    require_designated_channel,
    resolve_tournament,
)


class Leaderboard(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    leaderboard_group = app_commands.Group(
        name="leaderboard", description="Tournament leaderboards"
    )

    @leaderboard_group.command(
        name="show", description="Show the tournament leaderboard"
    )
    @app_commands.autocomplete(tournament=active_tournament_autocomplete)
    @app_commands.describe(tournament="Defaults to the single active tournament")
    async def leaderboard_show(
        self, interaction: discord.Interaction, tournament: Optional[int] = None
    ):
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

    @leaderboard_group.command(
        name="post",
        description="Post the live leaderboard board (admin, #🏁leaderboard only)",
    )
    @app_commands.autocomplete(tournament=active_tournament_autocomplete)
    @app_commands.describe(tournament="Defaults to the single active tournament")
    async def leaderboard_post(
        self, interaction: discord.Interaction, tournament: Optional[int] = None
    ):
        if not await require_designated_channel(interaction, "leaderboard"):
            return
        if not await require_admin(interaction):
            return
        t, err = await resolve_tournament(
            interaction, tournament, ["registration_open", "in_progress"]
        )
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        ok, reason = await leaderboard_render.post_leaderboard(
            self.bot, self.bot.db_path, str(interaction.guild_id), t["id"]
        )
        if not ok:
            await interaction.followup.send(
                f"❌ Couldn't post the leaderboard ({reason}).", ephemeral=True
            )
            return
        await interaction.followup.send(
            f"✅ Live leaderboard posted and pinned for **{t['name']}**. "
            "It'll stay up to date automatically as scores come in.",
            ephemeral=True,
        )

    @leaderboard_group.command(
        name="refresh", description="Refresh the live leaderboard now (admin)"
    )
    @app_commands.autocomplete(tournament=active_tournament_autocomplete)
    @app_commands.describe(tournament="Defaults to the single active tournament")
    async def leaderboard_refresh(
        self, interaction: discord.Interaction, tournament: Optional[int] = None
    ):
        if not await require_designated_channel(interaction, "leaderboard"):
            return
        if not await require_admin(interaction):
            return
        t, err = await resolve_tournament(
            interaction, tournament, ["registration_open", "in_progress"]
        )
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        if not t.get("leaderboard_message_id"):
            await interaction.followup.send(
                f"No leaderboard posted for **{t['name']}** yet — run "
                "`/leaderboard post` first.",
                ephemeral=True,
            )
            return
        updated = await leaderboard_render.refresh_leaderboard(
            self.bot, self.bot.db_path, t["id"]
        )
        await interaction.followup.send(
            "✅ Leaderboard refreshed." if updated
            else "⚠️ Couldn't reach the board message — it may have been deleted. "
                 "Run `/leaderboard post` to re-post it.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Leaderboard(bot))

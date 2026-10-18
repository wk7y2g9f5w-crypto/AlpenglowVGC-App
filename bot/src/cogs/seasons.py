"""Season standings: a multi-tournament points race. /season group.

Tournaments linked to the active season award points at /tournament complete
(100 for 1st down to 5 for 11th+; ties share the position's points).
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
    resolve_tournament,
)


async def _season_autocomplete(interaction: discord.Interaction,
                              current: str) -> list[app_commands.Choice]:
    rows = await db.list_seasons(interaction.client.db_path,
                                 str(interaction.guild_id))
    choices = [
        app_commands.Choice(name=f"{r['name']} [{r['status']}]"[:100],
                            value=r["id"])
        for r in rows
        if not current or current.lower() in r["name"].lower()
    ]
    return choices[:25]


async def _resolve_season(interaction: discord.Interaction,
                          season_id: Optional[int]
                          ) -> tuple[dict | None, str | None]:
    """Optional season id, defaulting to the guild's active season."""
    path = interaction.client.db_path
    guild_id = str(interaction.guild_id)
    if season_id:
        s = await db.get_season(path, season_id)
        if not s or s["guild_id"] != guild_id:
            return None, "❌ That season wasn't found in this server."
        return s, None
    s = await db.get_active_season(path, guild_id)
    if s:
        return s, None
    return None, "❌ No active season — an admin can start one with `/season create`."


def _standings_embed(s: dict, rows: list[dict], tournaments: list[dict]
                     ) -> discord.Embed:
    embed = discord.Embed(
        title=f"🏆 {s['name']} — Season Standings",
        description=("Points race across the season's tournaments "
                     "(100 for 1st, 90 for 2nd, … 5 for 11th+)."),
        color=0x2E7D32 if s["status"] == "completed" else 0x1B6CA8,
    )
    if not rows:
        embed.add_field(name="No points yet",
                        value="Points are awarded when a season tournament completes.",
                        inline=False)
    else:
        lines = []
        for i, r in enumerate(rows[:25], start=1):
            name = db.display_name_of(
                {"display_name": r["display_name"],
                 "golfplus_handle": r["golfplus_handle"]},
                r["discord_id"],
            )
            events = r["tournaments_played"]
            lines.append(f"**{i}.** {name} — **{r['total_points']} pts**"
                         f" ({events} event{'s' if events != 1 else ''})")
        embed.add_field(name="Standings", value="\n".join(lines), inline=False)
    if tournaments:
        tnames = ", ".join(t["name"] for t in tournaments[:10])
        more = f" (+{len(tournaments) - 10} more)" if len(tournaments) > 10 else ""
        embed.add_field(name="Season tournaments", value=tnames + more,
                        inline=False)
    return embed


class Seasons(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    season = app_commands.Group(
        name="season", description="Multi-tournament season points race"
    )

    @season.command(name="create", description="Start a new season (admin)")
    @app_commands.describe(name="Season name, e.g. 'Fall 2026'")
    async def season_create(self, interaction: discord.Interaction, name: str):
        if not await require_admin(interaction):
            return
        guild_id = str(interaction.guild_id)
        # Seasons run side by side (Cayden 2026-10-18): starting one
        # never requires ending another first.
        clean = name.strip()[:80]
        if not clean:
            await interaction.response.send_message(
                "❌ Give the season a name.", ephemeral=True
            )
            return
        sid = await db.create_season(self.bot.db_path, guild_id, clean,
                                     str(interaction.user.id))
        await interaction.response.send_message(
            f"✅ Season **{clean}** started! New tournaments pick it at "
            f"creation, or link existing ones with `/season add_tournament` "
            f"— they'll award season points when they complete. "
            f"(Season ID: {sid})"
        )

    @season.command(name="add_tournament",
                    description="Set which season a tournament counts toward (admin)")
    @app_commands.autocomplete(tournament=active_tournament_autocomplete,
                               season=_season_autocomplete)
    @app_commands.describe(
        tournament="Tournament to assign",
        season="Season it should count toward (required when several are active)",
    )
    async def season_add_tournament(self, interaction: discord.Interaction,
                                    tournament: Optional[int] = None,
                                    season: Optional[int] = None):
        if not await require_admin(interaction):
            return
        guild_id = str(interaction.guild_id)
        path = self.bot.db_path
        # Resolve the target season: the explicit pick, or the only
        # active season. Several active + no pick = ask, never guess.
        if season is not None:
            s = await db.get_season(path, season)
            if not s or s["guild_id"] != guild_id:
                await interaction.response.send_message(
                    "❌ That season wasn't found in this server.",
                    ephemeral=True,
                )
                return
            if s["status"] != "active":
                await interaction.response.send_message(
                    f"❌ Season **{s['name']}** has ended — its standings "
                    "are closed. Pick an active season.",
                    ephemeral=True,
                )
                return
        else:
            actives = await db.get_active_seasons(path, guild_id)
            if not actives:
                await interaction.response.send_message(
                    "❌ No active season — start one with `/season create` first.",
                    ephemeral=True,
                )
                return
            if len(actives) > 1:
                names = ", ".join(f"**{a['name']}**" for a in actives)
                await interaction.response.send_message(
                    f"❌ Several seasons are active ({names}) — run the "
                    "command again and pick which one this tournament "
                    "counts toward.",
                    ephemeral=True,
                )
                return
            s = actives[0]
        t, err = await resolve_tournament(
            interaction, tournament,
            ["registration_open", "in_progress", "completed"],
        )
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        # A tournament counts toward exactly ONE season: assigning it
        # here moves it out of any other season, points included
        # (Cayden 2026-10-18: mistakes must be correctable).
        current = await db.get_seasons_for_tournament(path, t["id"],
                                                      status=None)
        others = [c for c in current if c["id"] != s["id"]]
        if not others and any(c["id"] == s["id"] for c in current):
            await interaction.followup.send(
                f"ℹ️ **{t['name']}** already counts toward **{s['name']}**.",
                ephemeral=True,
            )
            return
        moved_from = ""
        for o in others:
            await db.delete_season_points(path, o["id"], t["id"])
            await db.remove_tournament_from_season(path, o["id"], t["id"])
        if others:
            moved_from = (f" Moved out of "
                          f"{', '.join('**' + o['name'] + '**' for o in others)}"
                          f" — its points there were removed.")
        await db.add_tournament_to_season(path, s["id"], t["id"])
        retro = ""
        if t["status"] == "completed":
            # Already finished: award its points right away (idempotent).
            rows = await leaderboard_render.final_standings_points(
                path, t["id"]
            )
            n = await db.record_season_points(
                path, s["id"], t["id"],
                [(r["player_discord_id"], r["position"], r["points"])
                 for r in rows],
            )
            retro = f" Its results were already final, so {n} player(s) were awarded points now."
        await interaction.followup.send(
            f"✅ **{t['name']}** now counts toward season "
            f"**{s['name']}**.{moved_from}{retro}",
            ephemeral=True,
        )

    @season.command(name="standings", description="Show the season points standings")
    @app_commands.autocomplete(season=_season_autocomplete)
    @app_commands.describe(season="Defaults to the active season")
    async def season_standings(self, interaction: discord.Interaction,
                               season: Optional[int] = None):
        s, err = await _resolve_season(interaction, season)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        rows = await db.get_season_standings(self.bot.db_path, s["id"])
        tournaments = await db.get_season_tournaments(self.bot.db_path, s["id"])
        await interaction.response.send_message(
            embed=_standings_embed(s, rows, tournaments), ephemeral=True
        )

    @season.command(name="complete",
                    description="Close the season & post final standings (admin)")
    @app_commands.autocomplete(season=_season_autocomplete)
    @app_commands.describe(season="Defaults to the active season")
    async def season_complete(self, interaction: discord.Interaction,
                              season: Optional[int] = None):
        if not await require_admin(interaction):
            return
        s, err = await _resolve_season(interaction, season)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        if s["status"] == "completed":
            await interaction.response.send_message(
                f"ℹ️ Season **{s['name']}** is already complete.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        await db.complete_season(self.bot.db_path, s["id"])
        s = await db.get_season(self.bot.db_path, s["id"])
        rows = await db.get_season_standings(self.bot.db_path, s["id"])
        tournaments = await db.get_season_tournaments(self.bot.db_path, s["id"])
        await interaction.channel.send(
            content=f"🏁 Season **{s['name']}** is complete! Final standings:",
            embed=_standings_embed(s, rows, tournaments),
        )
        await interaction.followup.send(
            f"✅ Season **{s['name']}** marked complete.", ephemeral=True
        )

    @season.command(name="list", description="List seasons in this server")
    async def season_list(self, interaction: discord.Interaction):
        rows = await db.list_seasons(self.bot.db_path, str(interaction.guild_id))
        if not rows:
            await interaction.response.send_message(
                "No seasons yet. An admin can start one with `/season create`.",
                ephemeral=True,
            )
            return
        embed = discord.Embed(title="🏆 Seasons", color=0x1B6CA8)
        for s in rows[:25]:
            status = "🟢 active" if s["status"] == "active" else "🏁 completed"
            n = s["tournament_count"]
            embed.add_field(
                name=f"{s['name']} (ID {s['id']})",
                value=f"{status} • {n} tournament{'s' if n != 1 else ''}",
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Seasons(bot))

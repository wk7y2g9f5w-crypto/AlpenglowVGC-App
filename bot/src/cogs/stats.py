"""Player career stats: /stats [player].

Aggregates verified tournament scorecards (individual cards only) into
round totals, scoring categories, and par streaks, plus match-play W-L-T.
"""
import json
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from src import db
from src import scoring_logic as sl


def _parse_pars(pars_text) -> list[int] | None:
    if not pars_text:
        return None
    try:
        return [int(x) for x in pars_text.split(",")]
    except ValueError:
        return None


class Stats(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="stats",
                          description="Show a player's career stats")
    @app_commands.describe(player="Defaults to you")
    async def stats(self, interaction: discord.Interaction,
                    player: Optional[discord.Member] = None):
        member = player or interaction.user
        pid = str(member.id)
        guild_id = str(interaction.guild_id)
        await interaction.response.defer(ephemeral=True)

        cards = await db.get_player_verified_cards(self.bot.db_path, guild_id,
                                                   pid)
        if not cards:
            await interaction.followup.send(
                f"📊 {member.display_name} has no verified tournament rounds yet.",
                ephemeral=True,
            )
            return

        rounds = []
        for c in cards:
            try:
                holes = json.loads(c["holes_json"])
            except (ValueError, TypeError):
                continue
            if not holes:
                continue
            rounds.append({"holes": holes, "pars": _parse_pars(c.get("pars"))})
        if not rounds:
            await interaction.followup.send(
                f"📊 {member.display_name} has no verified tournament rounds yet.",
                ephemeral=True,
            )
            return

        s = sl.career_stats(rounds)
        prow = await db.get_player(self.bot.db_path, pid)
        name = db.display_name_of(prow, pid)
        embed = discord.Embed(title=f"📊 {name} — Career Stats",
                              color=0x1B6CA8)

        best_line = f"**{s['best_total']}**"
        if s["best_to_par"] is not None:
            best_line += f" ({sl.format_to_par(s['best_to_par'])})"
        avg_line = f"{s['avg_total']:.1f}" if s["avg_total"] is not None else "—"
        embed.add_field(name="Verified rounds", value=str(s["rounds_played"]),
                        inline=True)
        embed.add_field(name="Best round", value=best_line, inline=True)
        embed.add_field(name="Average round", value=avg_line, inline=True)

        if s["best_par_streak"] is not None:
            scoring = (f"🐦 Birdies or better: **{s['birdies_or_better']}**\n"
                       f"⛳ Pars: **{s['pars_made']}**\n"
                       f"😅 Bogeys: **{s['bogeys']}**\n"
                       f"💥 Doubles or worse: **{s['doubles_or_worse']}**\n"
                       f"🔥 Best par-or-better streak: **{s['best_par_streak']}** holes")
            embed.add_field(name="Scoring", value=scoring, inline=False)
        else:
            embed.add_field(
                name="Scoring",
                value="Par data wasn't recorded for these rounds, so scoring "
                      "breakdowns aren't available.",
                inline=False,
            )

        matches = await db.list_confirmed_matches_for_player(
            self.bot.db_path, guild_id, pid
        )
        if matches:
            rec = sl.match_records(matches).get(pid, {"w": 0, "l": 0, "t": 0})
            embed.add_field(
                name="Match play",
                value=f"**{rec['w']}W {rec['l']}L {rec['t']}T**"
                      f" ({len(matches)} confirmed)",
                inline=True,
            )

        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Stats(bot))

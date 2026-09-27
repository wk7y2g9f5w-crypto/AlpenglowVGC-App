"""Match play: /report_match with opponent confirmation, /matches, admin override."""
from typing import Literal, Optional

import discord
from discord import app_commands
from discord.ext import commands

from src import db
from src import leaderboard_render
from src.cogs.common import (
    active_tournament_autocomplete,
    inprogress_tournament_autocomplete,
    require_admin,
    resolve_tournament,
)


class MatchConfirmView(discord.ui.View):
    """Persistent Confirm button for reported matches."""

    def __init__(self, match_id: int):
        super().__init__(timeout=None)
        button = discord.ui.Button(
            label="✅ Confirm result",
            style=discord.ButtonStyle.success,
            custom_id=f"mconfirm:{match_id}",
        )
        button.callback = self._on_confirm
        self.add_item(button)

    async def _on_confirm(self, interaction: discord.Interaction):
        match_id = int(interaction.data["custom_id"].split(":", 1)[1])
        # Imported lazily to avoid a hard cross-cog dependency at load time.
        from src.cogs.matches import confirm_match_flow

        await confirm_match_flow(interaction, match_id, confirmed_by_opponent=True)


async def confirm_match_flow(interaction: discord.Interaction, match_id: int,
                             confirmed_by_opponent: bool) -> None:
    """Shared confirm logic for the button and the admin override command."""
    db_path = interaction.client.db_path
    m = await db.get_match(db_path, match_id)
    if not m:
        await interaction.response.send_message(
            "❌ That match wasn't found.", ephemeral=True
        )
        return
    if m["status"] == "confirmed":
        await interaction.response.send_message(
            "That match is already confirmed.", ephemeral=True
        )
        return
    if confirmed_by_opponent:
        is_opponent = str(interaction.user.id) == m["player2"]
        if not is_opponent:
            # Admins may also confirm via the button.
            from src.cogs.common import is_admin

            if not await is_admin(interaction):
                await interaction.response.send_message(
                    "❌ Only the opponent can confirm this result.", ephemeral=True
                )
                return
    winner = m.get("winner") or "tie"  # reporter's claimed result, stored at report time
    await db.confirm_match(db_path, match_id, winner)
    await leaderboard_render.refresh_leaderboard(
        interaction.client, db_path, m["tournament_id"]
    )
    t = await db.get_tournament(db_path, m["tournament_id"])
    p1 = await db.get_player(db_path, m["player1"])
    p2 = await db.get_player(db_path, m["player2"])
    n1 = db.display_name_of(p1, m["player1"])
    n2 = db.display_name_of(p2, m["player2"])
    outcome = {"player1": f"{n1} wins", "player2": f"{n2} wins", "tie": "Tie"}.get(
        winner, winner
    )
    note = f" ({m['score_note']})" if m.get("score_note") else ""
    try:
        await interaction.response.send_message(
            f"✅ Match confirmed: **{n1}** vs **{n2}** — {outcome}{note}.",
            ephemeral=False,
        )
    except discord.HTTPException:
        pass
    # Disable the button on the original message if we can find it.
    try:
        msg = interaction.message
        if msg:
            view = discord.ui.View()
            done = discord.ui.Button(label="Confirmed ✅",
                                     style=discord.ButtonStyle.success,
                                     disabled=True)
            view.add_item(done)
            await msg.edit(view=view)
    except (discord.HTTPException, AttributeError):
        pass


class Matches(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        for m in await db.list_pending_matches(self.bot.db_path):
            self.bot.add_view(MatchConfirmView(m["id"]))

    @app_commands.command(name="report_match", description="Report a match-play result")
    @app_commands.autocomplete(tournament=inprogress_tournament_autocomplete)
    @app_commands.describe(
        opponent="Who you played",
        result="Result from YOUR perspective",
        score_note="e.g. '3&2', '1 up', '19 holes'",
        tournament="Defaults to the single in-progress tournament",
    )
    async def report_match(
        self,
        interaction: discord.Interaction,
        opponent: discord.Member,
        result: Literal["win", "loss", "tie"],
        score_note: Optional[str] = None,
        tournament: Optional[int] = None,
    ):
        t, err = await resolve_tournament(interaction, tournament, ["in_progress"])
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        if t["format"] != "match":
            await interaction.response.send_message(
                f"❌ **{t['name']}** is {t['format']} — match results only apply "
                "to match-play tournaments.",
                ephemeral=True,
            )
            return
        me, opp = str(interaction.user.id), str(opponent.id)
        if me == opp:
            await interaction.response.send_message(
                "❌ You can't report a match against yourself.", ephemeral=True
            )
            return
        for pid, label in ((me, "You"), (opp, opponent.display_name)):
            if not await db.is_registered(self.bot.db_path, t["id"], pid):
                await interaction.response.send_message(
                    f"❌ {label} {'are' if pid == me else 'is'} not registered for "
                    f"**{t['name']}**.",
                    ephemeral=True,
                )
                return
        # Store the reporter's claimed result; it becomes official on confirm.
        winner_hint = {"win": "player1", "loss": "player2", "tie": "tie"}[result]
        match_id = await db.create_match(
            self.bot.db_path, t["id"], me, opp, me, winner_hint,
            (score_note or "").strip()[:50] or None,
        )
        self.bot.add_view(MatchConfirmView(match_id))

        outcome_word = {"win": "won", "loss": "lost", "tie": "tied"}[result]
        note = f" ({score_note.strip()})" if score_note else ""
        await interaction.response.send_message(
            f"📝 Result reported: you {outcome_word} vs {opponent.display_name}{note}.\n"
            f"{opponent.mention}, hit **✅ Confirm result** below to lock it in "
            "(or talk to an admin if you disagree).",
            view=MatchConfirmView(match_id),
        )

    @app_commands.command(name="matches", description="List recent match results")
    @app_commands.autocomplete(tournament=active_tournament_autocomplete)
    async def matches(self, interaction: discord.Interaction,
                      tournament: Optional[int] = None):
        t, err = await resolve_tournament(
            interaction, tournament,
            ["registration_open", "in_progress", "completed"],
        )
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        rows = await db.list_matches(self.bot.db_path, t["id"])
        embed = discord.Embed(title=f"⚔️ {t['name']} — Matches", color=0x1B6CA8)
        if not rows:
            embed.description = "No matches reported yet."
        else:
            lines = []
            for m in rows[:20]:
                p1 = await db.get_player(self.bot.db_path, m["player1"])
                p2 = await db.get_player(self.bot.db_path, m["player2"])
                n1 = db.display_name_of(p1, m["player1"])
                n2 = db.display_name_of(p2, m["player2"])
                note = f" ({m['score_note']})" if m.get("score_note") else ""
                if m["status"] == "confirmed":
                    outcome = {"player1": f"{n1} won", "player2": f"{n2} won",
                               "tie": "Tie"}.get(m["winner"], "")
                    lines.append(f"• {n1} vs {n2}{note} — **{outcome}** ✅")
                else:
                    lines.append(f"• {n1} vs {n2}{note} — ⏳ awaiting confirmation")
            embed.description = "\n".join(lines)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="confirm_match", description="Confirm a reported match (admin override)")
    @app_commands.describe(match_id="Match ID from /matches",
                           winner="Who won (from the reporter's perspective player1 = reporter)")
    async def confirm_match(self, interaction: discord.Interaction, match_id: int,
                            winner: Literal["player1", "player2", "tie"]):
        if not await require_admin(interaction):
            return
        m = await db.get_match(self.bot.db_path, match_id)
        if not m:
            await interaction.response.send_message(
                f"❌ No match with ID {match_id}.", ephemeral=True
            )
            return
        if m["status"] == "confirmed":
            await interaction.response.send_message(
                "That match is already confirmed.", ephemeral=True
            )
            return
        await db.confirm_match(self.bot.db_path, match_id, winner)
        await leaderboard_render.refresh_leaderboard(
            self.bot, self.bot.db_path, m["tournament_id"]
        )
        await interaction.response.send_message(
            f"✅ Match #{match_id} confirmed (winner: {winner}).", ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Matches(bot))

"""Player registration: /register, /unregister, /roster + the Register button.

Eligibility is per-event registration: only registered players can join tee
times and submit scores.
"""
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from src import db
from src import teesheet as ts
from src.cogs.common import (
    open_tournament_autocomplete,
    active_tournament_autocomplete,
    resolve_tournament,
)


async def require_timezone(interaction: discord.Interaction) -> bool:
    """True when the player has a timezone set.

    Registration is gated on this: newcomers must run /set_timezone (or the
    /setup walkthrough) first so tee times are never ambiguous.
    Pure-ish helper, unit-testable with a fake interaction.
    """
    db_path = interaction.client.db_path
    member = interaction.user
    await db.upsert_player(db_path, str(member.id),
                           member.display_name[:80])
    if await db.get_timezone(db_path, str(member.id)):
        return True
    await interaction.response.send_message(
        "⏳ One quick setup step first: set your timezone with `/set_timezone` "
        "(or run the `/setup` walkthrough — 30 seconds), then register again.",
        ephemeral=True,
    )
    return False


async def do_register(interaction: discord.Interaction, tournament_id: int) -> None:
    """Shared registration flow used by /register and the Register button."""
    db_path = interaction.client.db_path
    t = await db.get_tournament(db_path, tournament_id)
    if not t or t["guild_id"] != str(interaction.guild_id):
        await interaction.response.send_message(
            "❌ That tournament wasn't found in this server.", ephemeral=True
        )
        return
    if t["status"] != "registration_open":
        await interaction.response.send_message(
            f"❌ Registration for **{t['name']}** is closed "
            f"(status: {t['status'].replace('_', ' ')}).",
            ephemeral=True,
        )
        return

    if not await require_timezone(interaction):
        return
    member = interaction.user
    new = await db.register_player(db_path, t["id"], str(interaction.user.id))

    # Per-tournament participant role: "⛳ <tournament name>"
    role_name = f"⛳ {t['name']}"[:100]
    role = discord.utils.get(interaction.guild.roles, name=role_name)
    role_note = ""
    if role is None:
        try:
            role = await interaction.guild.create_role(
                name=role_name, reason=f"Participant role for {t['name']}"
            )
        except discord.Forbidden:
            role_note = ("\n⚠️ I couldn't create your participant role — an admin "
                         "should check my role position (it must sit above roles I assign).")
    if role is not None:
        try:
            if isinstance(member, discord.Member):
                await member.add_roles(role)
        except discord.Forbidden:
            role_note = ("\n⚠️ I couldn't assign your participant role — an admin "
                         "should move my role above it in Server Settings → Roles.")

    if not new:
        await interaction.response.send_message(
            f"You're already registered for **{t['name']}**. ✅{role_note}",
            ephemeral=True,
        )
        return
    await interaction.response.send_message(
        f"✅ You're registered for **{t['name']}**! "
        f"Next: grab a tee time with `/tee_time create` or join one via `/tee_times`.{role_note}",
        ephemeral=True,
    )
    await ts.maybe_refresh(interaction.client, str(interaction.guild_id))


class Registration(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="register", description="Register for a tournament")
    @app_commands.autocomplete(tournament=open_tournament_autocomplete)
    @app_commands.describe(tournament="Defaults to the tournament open for registration")
    async def register(self, interaction: discord.Interaction,
                       tournament: Optional[int] = None):
        t, err = await resolve_tournament(interaction, tournament,
                                          ["registration_open"])
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        await do_register(interaction, t["id"])

    @app_commands.command(name="unregister", description="Withdraw from a tournament")
    @app_commands.autocomplete(tournament=active_tournament_autocomplete)
    @app_commands.describe(tournament="Defaults to the single active tournament")
    async def unregister(self, interaction: discord.Interaction,
                         tournament: Optional[int] = None):
        t, err = await resolve_tournament(
            interaction, tournament, ["registration_open", "in_progress"]
        )
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        removed = await db.unregister_player(
            self.bot.db_path, t["id"], str(interaction.user.id)
        )
        # Also drop them from all their tee times, if any.
        await db.leave_all_tee_times(self.bot.db_path, t["id"],
                                     str(interaction.user.id))
        if removed:
            await interaction.response.send_message(
                f"✅ You've been withdrawn from **{t['name']}**.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"You weren't registered for **{t['name']}**.", ephemeral=True
            )
        await ts.maybe_refresh(self.bot, str(interaction.guild_id))

    @app_commands.command(name="roster", description="Show registered players")
    @app_commands.autocomplete(tournament=active_tournament_autocomplete)
    @app_commands.describe(tournament="Defaults to the single active tournament")
    async def roster(self, interaction: discord.Interaction,
                     tournament: Optional[int] = None):
        t, err = await resolve_tournament(
            interaction, tournament, ["registration_open", "in_progress", "completed"]
        )
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        roster = await db.get_roster(self.bot.db_path, t["id"])
        embed = discord.Embed(
            title=f"📋 {t['name']} — Roster ({len(roster)})",
            color=0x1B6CA8,
        )
        if roster:
            lines = [f"{i}. {db.display_name_of(p, p['discord_id'])}"
                     for i, p in enumerate(roster[:50], start=1)]
            embed.description = "\n".join(lines)
            if len(roster) > 50:
                embed.set_footer(text=f"…and {len(roster) - 50} more")
        else:
            embed.description = "Nobody registered yet."
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Registration(bot))

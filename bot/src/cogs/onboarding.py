"""New-player onboarding: the /setup walkthrough.

/setup gives newcomers a 30-second pre-registration checklist:
1. pick their timezone (REQUIRED before they can register for anything —
   enforced by require_timezone in registration.py),
2. link their Golf+ username (optional; shows on boards and rosters).

#welcome diverts newcomers here so nobody hits the registration gate
confused.
"""
import discord
from discord import app_commands
from discord.ext import commands

from src import db
from src.cogs.profile import COMMON_TIMEZONES, MAX_HANDLE_LEN, clean_handle


def setup_checklist_text(tz_name: str | None, handle: str | None) -> str:
    """Render the setup checklist. Pure function, unit-testable."""
    lines = ["**Alpenglow VGC setup** — 30 seconds, then you're ready to play."]
    lines.append(
        "✅ **Timezone:** {}".format(tz_name) if tz_name
        else "⬜ **Timezone:** pick yours below — required before you can register"
    )
    lines.append(
        "✅ **Golf+ username:** {}".format(handle) if handle
        else "⬜ **Golf+ username:** link it so the crew can find you in-game (optional)"
    )
    if tz_name:
        lines.append(
            "\nYou're all set — grab a tournament from the **#tee-sheet** board "
            "or `/tournament list`, then hit Register. See you at golden hour. :sunset:"
        )
    else:
        lines.append("\nPick your timezone below to finish setup.")
    return "\n".join(lines)


class HandleModal(discord.ui.Modal, title="Link your Golf+ username"):
    handle = discord.ui.TextInput(
        label="Golf+ username",
        placeholder="Exactly as it appears in-game",
        max_length=MAX_HANDLE_LEN,
    )

    def __init__(self, view: "SetupView"):
        super().__init__()
        self._view = view

    async def on_submit(self, interaction: discord.Interaction):
        clean = clean_handle(self.handle.value)
        if clean is None:
            await interaction.response.send_message(
                f"❌ That doesn't look like a Golf+ username — "
                f"keep it to 1–{MAX_HANDLE_LEN} characters.",
                ephemeral=True,
            )
            return
        db_path = interaction.client.db_path
        uid = str(interaction.user.id)
        await db.upsert_player(db_path, uid, interaction.user.display_name[:80])
        await db.set_golfplus_handle(db_path, uid, clean)
        await self._view.refresh(interaction)


class TimezoneSelect(discord.ui.Select):
    def __init__(self, current_tz: str | None):
        options = [
            discord.SelectOption(label=label, value=tz,
                                 default=(tz == current_tz))
            for label, tz in COMMON_TIMEZONES
        ]
        super().__init__(
            placeholder="Pick your timezone…",
            options=options,
            custom_id="setup:tz",
        )

    async def callback(self, interaction: discord.Interaction):
        tz_name = self.values[0]
        db_path = interaction.client.db_path
        uid = str(interaction.user.id)
        await db.upsert_player(db_path, uid, interaction.user.display_name[:80])
        await db.set_timezone(db_path, uid, tz_name)
        await self.view.refresh(interaction)


class LinkHandleButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Link Golf+ username",
            style=discord.ButtonStyle.secondary,
            custom_id="setup:handle",
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(HandleModal(self.view))


class SetupView(discord.ui.View):
    """Ephemeral per-invocation setup panel."""

    def __init__(self, tz_name: str | None, handle: str | None):
        super().__init__(timeout=600)
        self.add_item(TimezoneSelect(tz_name))
        self.add_item(LinkHandleButton())

    async def refresh(self, interaction: discord.Interaction):
        db_path = interaction.client.db_path
        uid = str(interaction.user.id)
        tz = await db.get_timezone(db_path, uid)
        player = await db.get_player(db_path, uid)
        handle = (player or {}).get("golfplus_handle")
        self.clear_items()
        self.add_item(TimezoneSelect(tz))
        self.add_item(LinkHandleButton())
        await interaction.response.edit_message(
            content=setup_checklist_text(tz, handle), view=self
        )


class Onboarding(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="setup",
        description="New-player setup: timezone + Golf+ username (do this first!)",
    )
    async def setup(self, interaction: discord.Interaction):
        db_path = self.bot.db_path
        uid = str(interaction.user.id)
        await db.upsert_player(db_path, uid, interaction.user.display_name[:80])
        tz = await db.get_timezone(db_path, uid)
        player = await db.get_player(db_path, uid)
        handle = (player or {}).get("golfplus_handle")
        await interaction.response.send_message(
            setup_checklist_text(tz, handle),
            view=SetupView(tz, handle),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Onboarding(bot))

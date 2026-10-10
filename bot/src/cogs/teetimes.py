"""Tee times: create / list / join / leave, plus start-time reminders.

Rules:
- Players pick from open tee times or create their own (creator auto-joins).
- One tee time per player per tournament (joining elsewhere errors out —
  leave the first one first).
- Tee times respect max_players.
- A background loop nudges players 30 min and 5 min before their start.
  Times are entered in each player's own timezone (set with /set_timezone,
  default UTC) and stored as UTC; Discord renders them in each viewer's
  local timezone.
"""
import re
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from src import db
from src import teesheet as ts
from src.cogs.common import (
    active_tournament_autocomplete,
    inprogress_tournament_autocomplete,
    is_admin,
    resolve_tournament,
    resolve_tz,
    admin_role_mention,
    viewer_tee_time_when,
)

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


async def _tee_time_request_choices(interaction: discord.Interaction, current: str):
    """Autocomplete tee times across the guild's active tournaments.

    Times are shown in the viewing player's own timezone.
    """
    rows = await db.search_tee_times(
        interaction.client.db_path, str(interaction.guild_id), current or ""
    )
    choices = []
    for tt in rows:
        when = await viewer_tee_time_when(
            interaction.client.db_path, str(interaction.user.id),
            tt.get("starts_at"))
        name = f"{tt['tournament_name'][:30]} — {tt['label'][:40]}{when}"[:100]
        choices.append(app_commands.Choice(name=name, value=tt["id"]))
    return choices


def parse_in_tz(date_s: str, time_s: str, tz_name: str | None) -> datetime:
    """Parse YYYY-MM-DD + HH:MM (24h) in the given IANA timezone.

    Returns the instant as an aware UTC datetime (storage stays UTC).
    Raises ValueError on bad input. Pure function, unit-testable.
    """
    if not DATE_RE.match(date_s or ""):
        raise ValueError("Date must look like `2026-10-03` (YYYY-MM-DD).")
    if not TIME_RE.match(time_s or ""):
        raise ValueError("Time must look like `19:30` (24-hour HH:MM).")
    try:
        local = datetime.strptime(f"{date_s} {time_s}", "%Y-%m-%d %H:%M").replace(
            tzinfo=resolve_tz(tz_name)
        )
    except ValueError:
        raise ValueError("That date/time doesn't exist — double-check it.")
    return local.astimezone(timezone.utc)


async def _editable_tee_time_choices(interaction: discord.Interaction,
                                     current: str):
    """Autocomplete: tee times the caller may edit/delete.

    Creators see their own; admins see everything. Times are shown in the
    viewing player's own timezone.
    """
    admin = await is_admin(interaction)
    rows = await db.search_tee_times(
        interaction.client.db_path, str(interaction.guild_id), current or "")
    me = str(interaction.user.id)
    choices = []
    for tt in rows:
        if not admin and tt["created_by"] != me:
            continue
        when = await viewer_tee_time_when(
            interaction.client.db_path, me, tt.get("starts_at"))
        name = f"{tt['tournament_name'][:30]} — {tt['label'][:40]}{when}"[:100]
        choices.append(app_commands.Choice(name=name, value=tt["id"]))
    return choices

class TeeTimeView(discord.ui.View):
    """Request/Leave buttons for one /tee_times listing (short-lived)."""

    def __init__(self, tournament_id: int, tee_times: list[dict]):
        super().__init__(timeout=1800)  # 30 min; re-run /tee_times for fresh buttons
        self.tournament_id = tournament_id
        for tt in tee_times[:5]:
            req_btn = discord.ui.Button(
                label=f"Request: {tt['label'][:40]}",
                style=discord.ButtonStyle.primary,
                custom_id=f"ttreq:{tt['id']}",
            )
            req_btn.callback = self._make_request(tt["id"])
            self.add_item(req_btn)
        leave_btn = discord.ui.Button(
            label="Leave my tee time",
            style=discord.ButtonStyle.secondary,
            custom_id=f"ttleave:{tournament_id}",
        )
        leave_btn.callback = self._on_leave
        self.add_item(leave_btn)

    def _make_request(self, tee_time_id: int):
        async def _on_request(interaction: discord.Interaction):
            await request_join_flow(interaction, tee_time_id)

        return _on_request

    async def _on_leave(self, interaction: discord.Interaction):
        db_path = interaction.client.db_path
        left = await db.leave_all_tee_times(
            db_path, self.tournament_id, str(interaction.user.id)
        )
        if not left:
            await interaction.response.send_message(
                "You're not in any tee time for this tournament.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            f"✅ You left {left} tee time{'s' if left != 1 else ''}.",
            ephemeral=True,
        )
        await ts.maybe_refresh(interaction.client, str(interaction.guild_id))


class JoinRequestView(discord.ui.View):
    """Persistent Accept/Decline buttons for a tee-time join request.

    Posted in the tee time's channel, mentioning its creator.
    """

    def __init__(self, request_id: int):
        super().__init__(timeout=None)
        accept = discord.ui.Button(
            label="✅ Accept",
            style=discord.ButtonStyle.success,
            custom_id=f"jr_accept:{request_id}",
        )
        accept.callback = self._on_accept
        decline = discord.ui.Button(
            label="❌ Decline",
            style=discord.ButtonStyle.danger,
            custom_id=f"jr_decline:{request_id}",
        )
        decline.callback = self._on_decline
        self.add_item(accept)
        self.add_item(decline)

    async def _on_accept(self, interaction: discord.Interaction):
        await decide_join_request_flow(interaction, accept=True)

    async def _on_decline(self, interaction: discord.Interaction):
        await decide_join_request_flow(interaction, accept=False)


async def _request_channel(bot, tt: dict, interaction: discord.Interaction):
    """Best-effort channel for join-request messages: the tee time's home
    channel, falling back to the channel the interaction came from."""
    channel = None
    if tt.get("channel_id"):
        try:
            channel = bot.get_channel(int(tt["channel_id"]))
        except (ValueError, TypeError):
            channel = None
    return channel or interaction.channel


async def _disable_request_buttons(interaction: discord.Interaction, label: str):
    try:
        msg = interaction.message
        if msg:
            view = discord.ui.View()
            done = discord.ui.Button(label=label, disabled=True,
                                     style=discord.ButtonStyle.secondary)
            view.add_item(done)
            await msg.edit(view=view)
    except (discord.HTTPException, AttributeError):
        pass


async def request_join_flow(interaction: discord.Interaction, tee_time_id: int) -> None:
    """Request to join someone else's tee time. The creator approves/declines."""
    db_path = interaction.client.db_path
    user_id = str(interaction.user.id)
    tt = await db.get_tee_time(db_path, tee_time_id)
    if not tt:
        await interaction.response.send_message(
            "❌ That tee time no longer exists.", ephemeral=True
        )
        return
    t = await db.get_tournament(db_path, tt["tournament_id"])
    if not t or t["guild_id"] != str(interaction.guild_id):
        await interaction.response.send_message(
            "❌ That tee time isn't in this server.", ephemeral=True
        )
        return
    if t["status"] == "completed":
        await interaction.response.send_message(
            f"❌ **{t['name']}** is already complete.", ephemeral=True
        )
        return
    if not await db.is_registered(db_path, t["id"], user_id):
        await interaction.response.send_message(
            f"❌ Register for **{t['name']}** first (`/register`).", ephemeral=True
        )
        return
    existing = await db.get_player_tee_time(db_path, t["id"], user_id)
    if existing and existing["id"] != tt["id"]:
        active = await db.tee_time_for_round(
            db_path, t["id"], tt.get("round_number") or 1, user_id,
            exclude_tee_time_id=tt["id"],
        )
        if active is not None:
            await interaction.response.send_message(
                f"❌ You're already in **{active['label']}** for "
                f"Round {active.get('round_number') or 1}. Leave it first, then "
                "request this one (one tee time per player per round).",
                ephemeral=True,
            )
            return
    if existing and existing["id"] == tt["id"]:
        await interaction.response.send_message(
            f"You're already in **{tt['label']}**. ✅", ephemeral=True
        )
        return

    result = await db.create_join_request(db_path, tt["id"], user_id)
    if result == "full":
        await interaction.response.send_message(
            f"❌ **{tt['label']}** is full ({tt['max_players']} players).",
            ephemeral=True,
        )
        return
    if result == "pending":
        await interaction.response.send_message(
            f"⏳ You already have a pending request for **{tt['label']}** — "
            "the creator has been notified.",
            ephemeral=True,
        )
        return
    if result != "ok":
        await interaction.response.send_message(
            "❌ Couldn't create that request — try again.", ephemeral=True
        )
        return

    req = await db.get_pending_request(db_path, tt["id"], user_id)
    player = await db.get_player(db_path, user_id)
    requester_name = db.display_name_of(player, user_id)
    creator = await db.get_player(db_path, tt["created_by"])
    creator_name = db.display_name_of(creator, tt["created_by"])

    channel = await _request_channel(interaction.client, tt, interaction)
    try:
        await channel.send(
            f"⛳ **{requester_name}** wants to join **{tt['label']}** "
            f"({t['name']}).\n<@{tt['created_by']}> ({creator_name}), "
            "accept or decline below:",
            view=JoinRequestView(req["id"]),
        )
    except (discord.HTTPException, AttributeError):
        await interaction.response.send_message(
            "❌ Couldn't reach the tee-time channel — ask the creator directly.",
            ephemeral=True,
        )
        return
    interaction.client.add_view(JoinRequestView(req["id"]))
    await interaction.response.send_message(
        f"✅ Request sent! **{creator_name}** has been notified — you'll join "
        f"**{tt['label']}** as soon as they accept.",
        ephemeral=True,
    )
    await ts.maybe_refresh(interaction.client, str(interaction.guild_id))


async def decide_join_request_flow(interaction: discord.Interaction, accept: bool) -> None:
    """Handle an Accept/Decline click. Only the tee-time creator (or an admin)
    may decide."""
    await _decide_join_request_flow(interaction, accept)
    await ts.maybe_refresh(interaction.client, str(interaction.guild_id))


async def _decide_join_request_flow(interaction: discord.Interaction, accept: bool) -> None:
    """Handle an Accept/Decline click. Only the tee-time creator (or an admin)
    may decide."""
    db_path = interaction.client.db_path
    request_id = int(interaction.data["custom_id"].split(":", 1)[1])
    req = await db.get_join_request(db_path, request_id)
    if not req:
        await interaction.response.send_message(
            "❌ That request no longer exists.", ephemeral=True
        )
        return
    if req["status"] != "pending":
        await interaction.response.send_message(
            "That request was already handled.", ephemeral=True
        )
        await _disable_request_buttons(interaction, f"Handled ({req['status']})")
        return
    tt = await db.get_tee_time(db_path, req["tee_time_id"])
    t = await db.get_tournament(db_path, tt["tournament_id"]) if tt else None
    if not tt or not t:
        await interaction.response.send_message(
            "❌ The tee time for that request no longer exists.", ephemeral=True
        )
        return

    decider_id = str(interaction.user.id)
    if decider_id != tt["created_by"]:
        from src.cogs.common import is_admin

        if not await is_admin(interaction):
            await interaction.response.send_message(
                "❌ Only the tee-time creator (or an admin) can decide this.",
                ephemeral=True,
            )
            return

    player = await db.get_player(db_path, req["player_discord_id"])
    requester_name = db.display_name_of(player, req["player_discord_id"])
    channel = await _request_channel(interaction.client, tt, interaction)

    async def _announce(text: str):
        try:
            await channel.send(text)
        except (discord.HTTPException, AttributeError):
            pass

    if not accept:
        await db.decide_join_request(db_path, request_id, "declined", decider_id)
        await _disable_request_buttons(interaction, "Declined ❌")
        await interaction.response.send_message(
            f"You declined **{requester_name}**'s request.", ephemeral=True
        )
        await _announce(
            f"❌ <@{req['player_discord_id']}> — your request to join "
            f"**{tt['label']}** was declined."
        )
        return

    # Accept: re-validate everything, since the world may have changed while
    # the request sat pending.
    if not await db.is_registered(db_path, t["id"], req["player_discord_id"]):
        await db.decide_join_request(db_path, request_id, "declined", decider_id)
        await _disable_request_buttons(interaction, "Declined ❌")
        await interaction.response.send_message(
            f"❌ **{requester_name}** is no longer registered — request declined.",
            ephemeral=True,
        )
        return
    other = await db.tee_time_for_round(
        db_path, t["id"], tt.get("round_number") or 1,
        req["player_discord_id"], exclude_tee_time_id=tt["id"],
    )
    if other is not None:
        await db.decide_join_request(db_path, request_id, "declined", decider_id)
        await _disable_request_buttons(interaction, "Declined ❌")
        await interaction.response.send_message(
            f"❌ **{requester_name}** is already in **{other['label']}** for "
            f"Round {other.get('round_number') or 1} — request declined.",
            ephemeral=True,
        )
        return
    # Play-in-order: the requester needs a submitted card for the previous
    # round, unless the decider is an admin (admin override).
    rnd_num = tt.get("round_number") or 1
    if rnd_num > 1 and not await db.has_completed_round_card(
        db_path, t["id"], req["player_discord_id"], rnd_num - 1
    ):
        if not await is_admin(interaction):
            await db.decide_join_request(
                db_path, request_id, "declined", decider_id)
            await _disable_request_buttons(interaction, "Declined ❌")
            await interaction.response.send_message(
                f"❌ **{requester_name}** hasn't submitted Round {rnd_num - 1} "
                f"— rounds are played in order. Request declined.",
                ephemeral=True,
            )
            return
    join_result = await db.join_tee_time(db_path, tt["id"], req["player_discord_id"])
    if join_result == "round_conflict":
        await db.decide_join_request(db_path, request_id, "declined", decider_id)
        await _disable_request_buttons(interaction, "Declined ❌")
        await interaction.response.send_message(
            f"❌ **{requester_name}** joined another Round "
            f"{tt.get('round_number') or 1} tee time while this was pending — "
            "request auto-declined.",
            ephemeral=True,
        )
        return
    if join_result == "full":
        await db.decide_join_request(db_path, request_id, "declined", decider_id)
        await _disable_request_buttons(interaction, "Declined ❌")
        await interaction.response.send_message(
            f"❌ **{tt['label']}** filled up — request auto-declined.", ephemeral=True
        )
        await _announce(
            f"❌ <@{req['player_discord_id']}> — **{tt['label']}** filled up "
            "before your request was accepted."
        )
        return
    if join_result == "started":
        await db.decide_join_request(db_path, request_id, "declined", decider_id)
        await _disable_request_buttons(interaction, "Declined ❌")
        await interaction.response.send_message(
            f"❌ **{tt['label']}** has already started — request auto-declined.",
            ephemeral=True,
        )
        return
    await db.decide_join_request(db_path, request_id, "accepted", decider_id)
    await _disable_request_buttons(interaction, "Accepted ✅")
    count = await db.tee_time_player_count(db_path, tt["id"])
    await interaction.response.send_message(
        f"✅ **{requester_name}** is in **{tt['label']}** "
        f"({count}/{tt['max_players']}).",
        ephemeral=True,
    )
    await _announce(
        f"✅ <@{req['player_discord_id']}> — **{requester_name}**, "
        f"you're in **{tt['label']}** ({count}/{tt['max_players']})!"
    )


class TeeTimes(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        if not self.reminder_loop.is_running():
            self.reminder_loop.start()
        # Re-attach persistent Accept/Decline buttons for pending requests.
        for r in await db.list_pending_join_requests(self.bot.db_path):
            self.bot.add_view(JoinRequestView(r["id"]))

    async def cog_unload(self):
        self.reminder_loop.cancel()

    # ------------------------------ reminder loop ---------------------------
    @tasks.loop(minutes=1)
    async def reminder_loop(self):
        now = datetime.now(timezone.utc)
        horizon = now + timedelta(minutes=40)
        for tt in await db.get_reminder_due(
            self.bot.db_path, now.isoformat(), horizon.isoformat()
        ):
            try:
                starts = datetime.fromisoformat(tt["starts_at"])
            except (ValueError, TypeError):
                continue
            mins = (starts - now).total_seconds() / 60
            kind = None
            if 0 < mins <= 5 and not tt["reminded_5"]:
                kind = "5"
            elif 0 < mins <= 30 and not tt["reminded_30"]:
                kind = "30"
            if not kind:
                continue
            players = await db.get_tee_time_players(self.bot.db_path, tt["id"])
            if not players or not tt.get("channel_id"):
                await db.mark_reminded(self.bot.db_path, tt["id"], kind)
                continue
            channel = self.bot.get_channel(int(tt["channel_id"]))
            if channel is None:
                await db.mark_reminded(self.bot.db_path, tt["id"], kind)
                continue
            mentions = " ".join(f"<@{p['discord_id']}>" for p in players)
            t = await db.get_tournament(self.bot.db_path, tt["tournament_id"])
            tname = t["name"] if t else "the tournament"
            if kind == "30":
                text = (f"⏰ **30 minutes** until **{tt['label']}** ({tname})! "
                        f"{mentions} — get warmed up.")
            else:
                text = (f"🏌️ **Head to the tee!** **{tt['label']}** ({tname}) "
                        f"starts in ~5 minutes. {mentions}")
            try:
                await channel.send(text)
            except discord.HTTPException:
                pass
            await db.mark_reminded(self.bot.db_path, tt["id"], kind)

    @reminder_loop.before_loop
    async def _before_reminders(self):
        await self.bot.wait_until_ready()

    # ------------------------------- commands -------------------------------
    tee_time = app_commands.Group(name="tee_time", description="Manage tee times")

    @tee_time.command(name="create", description="Create a tee time (time is in YOUR timezone)")
    @app_commands.autocomplete(tournament=active_tournament_autocomplete)
    @app_commands.describe(
        tournament="Defaults to the single active tournament",
        date="Date as YYYY-MM-DD",
        time="Start time as HH:MM (24-hour, your local time — set with /set_timezone)",
        label="Optional label, e.g. 'Friday flight A'",
        max_players="Max players (default 4)",
        round="Which tournament round this tee time is for (default: 1)",
    )
    async def tee_time_create(
        self,
        interaction: discord.Interaction,
        date: str,
        time: str,
        tournament: Optional[int] = None,
        label: Optional[str] = None,
        max_players: int = 4,
        round: Literal[1, 2, 3, 4, 5] = 1,
    ):
        t, err = await resolve_tournament(
            interaction, tournament, ["registration_open", "in_progress"]
        )
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        if not await db.is_registered(self.bot.db_path, t["id"],
                                      str(interaction.user.id)):
            await interaction.response.send_message(
                f"❌ Register for **{t['name']}** first (`/register`).", ephemeral=True
            )
            return
        tz_name = await db.get_timezone(self.bot.db_path, str(interaction.user.id))
        try:
            starts = parse_in_tz(date, time, tz_name)
        except ValueError as e:
            await interaction.response.send_message(f"❌ {e}", ephemeral=True)
            return
        if not 2 <= max_players <= 8:
            await interaction.response.send_message(
                "❌ max_players must be between 2 and 8.", ephemeral=True
            )
            return
        if starts < datetime.now(timezone.utc):
            await interaction.response.send_message(
                "❌ That time is in the past — pick a future tee time.", ephemeral=True
            )
            return
        if await db.get_round(self.bot.db_path, t["id"], round) is None:
            await interaction.response.send_message(
                f"❌ Round {round} doesn't exist in **{t['name']}**.", ephemeral=True
            )
            return
        conflict = await db.tee_time_for_round(
            self.bot.db_path, t["id"], round, str(interaction.user.id)
        )
        if conflict is not None:
            await interaction.response.send_message(
                f"❌ You're already in **{conflict['label']}** for Round {round} "
                "(one tee time per player per round). Leave it first to create "
                "a different one.",
                ephemeral=True,
            )
            return

        tz_abbrev = starts.astimezone(resolve_tz(tz_name)).strftime("%Z")
        label = (label or f"Tee time {date} {time} {tz_abbrev}").strip()[:80]
        tt_id = await db.create_tee_time(
            self.bot.db_path, t["id"], label, starts.isoformat(), max_players,
            str(interaction.user.id), str(interaction.channel_id),
            round_number=round,
        )
        await db.join_tee_time(self.bot.db_path, tt_id, str(interaction.user.id))
        unix = int(starts.timestamp())
        await interaction.response.send_message(
            f"✅ Tee time **{label}** created for **{t['name']}** (Round {round}) — "
            f"<t:{unix}:F> (<t:{unix}:R>). You're in (1/{max_players}). "
            f"Others can request to join from `/tee_times` — you'll approve "
            f"them right here in this channel.",
            ephemeral=False,
        )
        await ts.maybe_refresh(self.bot, str(interaction.guild_id))

    @tee_time.command(name="request", description="Request to join someone's tee time (they approve it)")
    @app_commands.autocomplete(tee_time=_tee_time_request_choices)
    @app_commands.describe(tee_time="Which tee time you want to join")
    async def tee_time_request(self, interaction: discord.Interaction, tee_time: int):
        await request_join_flow(interaction, tee_time)

    @tee_time.command(name="leave", description="Leave your tee time")
    @app_commands.autocomplete(tournament=active_tournament_autocomplete)
    async def tee_time_leave(
        self, interaction: discord.Interaction, tournament: Optional[int] = None
    ):
        t, err = await resolve_tournament(
            interaction, tournament, ["registration_open", "in_progress"]
        )
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        tt = await db.get_player_tee_time(self.bot.db_path, t["id"],
                                          str(interaction.user.id))
        if not tt:
            await interaction.response.send_message(
                "You're not in any tee time for this tournament.", ephemeral=True
            )
            return
        await db.leave_tee_time(self.bot.db_path, tt["id"], str(interaction.user.id))
        await interaction.response.send_message(
            f"✅ You left **{tt['label']}**.", ephemeral=True
        )

    @staticmethod
    async def _manageable_tee_time(interaction: discord.Interaction,
                                   tee_time_id: int):
        """Fetch a tee time and check the caller may edit/delete it.

        Returns (tee_time, tournament, error_message). Only the creator or
        an admin may manage a tee time.
        """
        bot = interaction.client
        tt = await db.get_tee_time(bot.db_path, tee_time_id)
        if not tt:
            return None, None, "❌ That tee time doesn't exist."
        t = await db.get_tournament(bot.db_path, tt["tournament_id"])
        if not t or t["guild_id"] != str(interaction.guild_id):
            return None, None, "❌ That tee time doesn't exist."
        me = str(interaction.user.id)
        if tt["created_by"] != me and not await is_admin(interaction):
            return None, None, (
                "❌ Only the tee time creator or an admin can change it.")
        return tt, t, None

    @tee_time.command(name="edit",
                      description="Fix a tee time's name, date, time or round")
    @app_commands.autocomplete(tee_time=_editable_tee_time_choices)
    @app_commands.describe(
        tee_time="Which tee time to edit",
        label="New name (leave blank to keep the current one)",
        date="New date as YYYY-MM-DD (leave blank to keep)",
        time="New start time as HH:MM, 24-hour, your local time (blank to keep)",
        round="Move to a different tournament round",
    )
    async def tee_time_edit(
        self,
        interaction: discord.Interaction,
        tee_time: int,
        label: Optional[str] = None,
        date: Optional[str] = None,
        time: Optional[str] = None,
        round: Optional[Literal[1, 2, 3, 4, 5]] = None,
    ):
        tt, t, err = await self._manageable_tee_time(interaction, tee_time)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        if label is None and date is None and time is None and round is None:
            await interaction.response.send_message(
                "❌ Nothing to change — give me a new `label`, `date`, "
                "`time` or `round`.", ephemeral=True)
            return
        if round is not None and await db.get_round(
                self.bot.db_path, t["id"], round) is None:
            await interaction.response.send_message(
                f"❌ Round {round} doesn't exist in **{t['name']}**.",
                ephemeral=True)
            return
        new_label = label.strip()[:80] if label and label.strip() else None
        starts_at = None
        if date is not None or time is not None:
            # Fill whichever half wasn't given from the current start time,
            # interpreted in the editor's own timezone.
            tz_name = await db.get_timezone(
                self.bot.db_path, str(interaction.user.id))
            cur = datetime.fromisoformat(tt["starts_at"]).astimezone(
                resolve_tz(tz_name))
            try:
                starts = parse_in_tz(
                    date or cur.strftime("%Y-%m-%d"),
                    time or cur.strftime("%H:%M"),
                    tz_name,
                )
            except ValueError as e:
                await interaction.response.send_message(
                    f"❌ {e}", ephemeral=True)
                return
            starts_at = starts.isoformat()
        changed = await db.update_tee_time(
            self.bot.db_path, tee_time, label=new_label, starts_at=starts_at,
            round_number=round)
        if not changed:
            await interaction.response.send_message(
                "Nothing changed.", ephemeral=True)
            return
        await ts.maybe_refresh(self.bot, str(interaction.guild_id))
        bits = []
        if new_label:
            bits.append(f"name → **{new_label}**")
        if starts_at:
            unix = int(datetime.fromisoformat(starts_at).timestamp())
            bits.append(f"start → <t:{unix}:F> (<t:{unix}:R>)")
        if round is not None:
            bits.append(f"round → **Round {round}**")
        await interaction.response.send_message(
            f"✅ Tee time updated for **{t['name']}**: {'; '.join(bits)}.",
            ephemeral=False,
        )

    @tee_time.command(name="delete",
                      description="Delete a tee time (creator or admin)")
    @app_commands.autocomplete(tee_time=_editable_tee_time_choices)
    @app_commands.describe(tee_time="Which tee time to delete")
    async def tee_time_delete(
        self, interaction: discord.Interaction, tee_time: int
    ):
        tt, t, err = await self._manageable_tee_time(interaction, tee_time)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        n_cards = await db.count_tee_time_scorecards(
            self.bot.db_path, tee_time)
        if n_cards:
            await interaction.response.send_message(
                f"❌ **{tt['label']}** already has {n_cards} submitted "
                f"scorecard{'s' if n_cards != 1 else ''} — scores must be "
                f"deleted first. Deleting the tee time now would orphan them.",
                ephemeral=True)
            return
        players = await db.get_tee_time_players(self.bot.db_path, tee_time)

        class ConfirmDelete(discord.ui.View):
            def __init__(self, cog: "TeeTimes"):
                super().__init__(timeout=60)
                self.cog = cog

            @discord.ui.button(label="Delete it", style=discord.ButtonStyle.danger)
            async def confirm(self, btn_interaction: discord.Interaction,
                              button: discord.ui.Button):
                # Re-check permission on the click (roles can change).
                _, _, err2 = await self.cog._manageable_tee_time(
                    btn_interaction, tee_time)
                if err2:
                    await btn_interaction.response.send_message(
                        err2, ephemeral=True)
                    return
                await db.delete_tee_time(btn_interaction.client.db_path,
                                         tee_time)
                await ts.maybe_refresh(
                    btn_interaction.client, str(btn_interaction.guild_id))
                await btn_interaction.response.edit_message(
                    content=f"🗑️ Tee time **{tt['label']}** deleted.",
                    view=None)

            @discord.ui.button(label="Keep it", style=discord.ButtonStyle.secondary)
            async def cancel(self, btn_interaction: discord.Interaction,
                             button: discord.ui.Button):
                await btn_interaction.response.edit_message(
                    content="Kept — nothing deleted.", view=None)

        await interaction.response.send_message(
            f"Delete **{tt['label']}** from **{t['name']}**? "
            f"({len(players)} player{'s' if len(players) != 1 else ''} "
            f"will be removed from it.) This can't be undone.",
            view=ConfirmDelete(self),
            ephemeral=True,
        )

    @app_commands.command(name="tee_times", description="Show open tee times with Join buttons")
    @app_commands.autocomplete(tournament=active_tournament_autocomplete)
    @app_commands.describe(tournament="Defaults to the single active tournament")
    async def tee_times(self, interaction: discord.Interaction,
                        tournament: Optional[int] = None):
        t, err = await resolve_tournament(
            interaction, tournament, ["registration_open", "in_progress"]
        )
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        tee_times = await db.list_tee_times(self.bot.db_path, t["id"])
        if not tee_times:
            await interaction.response.send_message(
                f"No tee times yet for **{t['name']}**. Create one with "
                "`/tee_time create` (times are UTC).",
                ephemeral=True,
            )
            return
        embed = discord.Embed(
            title=f"⛳ {t['name']} — Tee Times",
            description="Request to join a tee time below (the creator approves), "
                        "or create your own with `/tee_time create`.",
            color=0x1B6CA8,
        )
        for tt in tee_times[:10]:
            players = await db.get_tee_time_players(self.bot.db_path, tt["id"])
            names = ", ".join(db.display_name_of(p, p["discord_id"])
                              for p in players[:8])
            if len(players) > 8:
                names += f" (+{len(players) - 8} more)"
            when = ""
            if tt.get("starts_at"):
                try:
                    unix = int(datetime.fromisoformat(tt["starts_at"]).timestamp())
                    when = f"<t:{unix}:F> (<t:{unix}:R>)\n"
                except (ValueError, TypeError):
                    pass
            spots = tt["max_players"] - len(players)
            status = f"{len(players)}/{tt['max_players']} players"
            if spots <= 0:
                status += " — **FULL**"
            pending = await db.list_pending_join_requests(self.bot.db_path, tt["id"])
            if pending:
                status += f" — ⏳ {len(pending)} request(s) awaiting approval"
            value = f"{when}{status}"
            if names:
                value += f"\n{names}"
            embed.add_field(name=f"🕐 {tt['label']} (ID {tt['id']})",
                            value=value, inline=False)
        if len(tee_times) > 10:
            embed.set_footer(text=f"Showing 10 of {len(tee_times)} tee times")
        view = TeeTimeView(t["id"], tee_times)
        await interaction.response.send_message(embed=embed, view=view)


async def setup(bot: commands.Bot):
    await bot.add_cog(TeeTimes(bot))

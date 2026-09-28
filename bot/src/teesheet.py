"""The Tee Sheet: persistent, auto-updating boards for registration and tee times.

Two bot-managed messages live in the #tee-sheet channel:

- the **registration board** lists tournaments open for registration, each
  with a Register button;
- the **tee sheet** lists upcoming tee times across the active tournaments,
  each with a Request button (the tee-time creator approves, as usual).

Both boards refresh automatically after every event that changes them:
tee time created/joined/left, join request sent/decided, registration
changes, and tournament created/started/completed.

Buttons are persistent (timeout=None + custom_id) so the board survives bot
restarts. View instances are cached on the bot and re-registered in
``startup``; refreshes rebuild the buttons on the cached instance rather
than swapping instances, so ``add_view`` mappings never go stale.
"""

from datetime import datetime, timedelta, timezone

import discord

from src import db
from src import scoring_logic as sl

REGISTER_KIND = "register"
TEESHEET_KIND = "teesheet"

MAX_REGISTER_BUTTONS = 10
MAX_TEETIME_BUTTONS = 10
# Tee times that started more than this long ago drop off the board.
STALE_AFTER = timedelta(hours=3)

FORMAT_LABELS = {
    "stroke": "Stroke play",
    "match": "Match play",
    "best_ball": "Best ball",
    "alt_shot": "Alternate shot",
    "scramble": "Scramble",
}


# ------------------------------------------------------------- persistent views
class BoardRegisterView(discord.ui.View):
    """Persistent Register buttons for the registration board."""

    def __init__(self):
        super().__init__(timeout=None)

    def rebuild(self, tournaments: list[dict]) -> None:
        self.clear_items()
        for t in tournaments[:MAX_REGISTER_BUTTONS]:
            btn = discord.ui.Button(
                label=f"✅ Register: {t['name'][:50]}",
                style=discord.ButtonStyle.success,
                custom_id=f"board_reg:{t['id']}",
            )
            btn.callback = self._make_register(t["id"])
            self.add_item(btn)

    @staticmethod
    def _make_register(tournament_id: int):
        async def _on_register(interaction: discord.Interaction):
            from src.cogs.registration import do_register

            await do_register(interaction, tournament_id)
            await maybe_refresh(interaction.client, str(interaction.guild_id))

        return _on_register


class BoardTeeSheetView(discord.ui.View):
    """Persistent Request/Leave buttons for the tee sheet board."""

    def __init__(self):
        super().__init__(timeout=None)

    def rebuild(self, sections: list[tuple[dict, list[dict]]]) -> None:
        """sections: [(tournament, [tee_time, ...]), ...]."""
        self.clear_items()
        for t, tee_times in sections:
            for tt in tee_times:
                btn = discord.ui.Button(
                    label=f"Request: {tt['label'][:40]}",
                    style=discord.ButtonStyle.primary,
                    custom_id=f"board_ttreq:{tt['id']}",
                )
                btn.callback = self._make_request(tt["id"])
                self.add_item(btn)
            leave = discord.ui.Button(
                label=f"Leave ({t['name'][:28]})",
                style=discord.ButtonStyle.secondary,
                custom_id=f"board_ttleave:{t['id']}",
            )
            leave.callback = self._make_leave(t["id"])
            self.add_item(leave)

    @staticmethod
    def _make_request(tee_time_id: int):
        async def _on_request(interaction: discord.Interaction):
            from src.cogs.teetimes import request_join_flow

            await request_join_flow(interaction, tee_time_id)
            await maybe_refresh(interaction.client, str(interaction.guild_id))

        return _on_request

    @staticmethod
    def _make_leave(tournament_id: int):
        async def _on_leave(interaction: discord.Interaction):
            db_path = interaction.client.db_path
            tt = await db.get_player_tee_time(
                db_path, tournament_id, str(interaction.user.id)
            )
            if not tt:
                await interaction.response.send_message(
                    "You're not in a tee time for that tournament.",
                    ephemeral=True,
                )
                return
            await db.leave_tee_time(db_path, tt["id"], str(interaction.user.id))
            await interaction.response.send_message(
                f"✅ You left **{tt['label']}**.", ephemeral=True
            )
            await maybe_refresh(interaction.client, str(interaction.guild_id))

        return _on_leave


# ------------------------------------------------------------------ builders
def _when_line(starts_at) -> str:
    if not starts_at:
        return ""
    try:
        dt = datetime.fromisoformat(starts_at)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        unix = int(dt.timestamp())
        return f"<t:{unix}:F> (<t:{unix}:R>)\n"
    except (ValueError, TypeError):
        return ""


async def _upcoming_tee_times(db_path, guild_id):
    """[(tournament, tee_time, players, pending), ...], soonest first."""
    out = []
    tournaments = await db.list_tournaments(
        db_path, guild_id, statuses=["registration_open", "in_progress"]
    )
    for t in tournaments:
        for tt in await db.list_tee_times(db_path, t["id"]):
            starts_at = tt.get("starts_at")
            if starts_at:
                try:
                    dt = datetime.fromisoformat(starts_at)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    if dt < datetime.now(timezone.utc) - STALE_AFTER:
                        continue
                except ValueError:
                    pass
            players = await db.get_tee_time_players(db_path, tt["id"])
            pending = await db.list_pending_join_requests(db_path, tt["id"])
            out.append((t, tt, players, pending))
    out.sort(key=lambda row: row[1].get("starts_at") or "")
    return out


def _tee_time_value(tt: dict, players: list[dict], pending: list[dict]) -> str:
    spots = tt["max_players"] - len(players)
    status = f"{len(players)}/{tt['max_players']} players"
    if spots <= 0:
        status += " — **FULL**"
    if pending:
        status += f" — ⏳ {len(pending)} request(s) awaiting approval"
    value = f"{_when_line(tt.get('starts_at'))}{status}"
    if players:
        names = ", ".join(db.display_name_of(p, p["discord_id"])
                           for p in players[:8])
        if len(players) > 8:
            names += f" (+{len(players) - 8} more)"
        value += f"\n{names}"
    return value


async def _rounds_line(db_path: str, tournament_id: int) -> str:
    """Compact per-round settings line, or '' for single-round events."""
    rounds = await db.list_rounds(db_path, tournament_id)
    if len(rounds) <= 1:
        return ""
    bits = []
    for r in rounds:
        tee = sl.TEE_LABELS.get(r["tee_position"], r["tee_position"])
        pin = sl.PIN_LABELS.get(r["pin_position"], r["pin_position"])
        wind = sl.WIND_LABELS.get(r["wind_strength"], r["wind_strength"])
        dates = sl.format_date_range(r.get("start_date"), r.get("end_date"))
        bits.append(f"R{r['round_number']} ({dates}) {tee}/{pin}/{wind}")
    return f"🔁 {len(rounds)} rounds: " + " · ".join(bits)


async def build_register_board(bot, guild_id):
    """Returns (embed, tournaments) for the registration board."""
    tournaments = await db.list_tournaments(
        bot.db_path, guild_id, statuses=["registration_open"]
    )
    embed = discord.Embed(title="📋 Open for Registration", color=0x1B6CA8)
    if not tournaments:
        embed.description = (
            "No tournaments open for registration right now. Check back soon!"
        )
    else:
        embed.description = (
            "Tap **Register** under the event you want — "
            "play in none, some, or all of them."
        )
    for t in tournaments[:MAX_REGISTER_BUTTONS]:
        roster = await db.get_roster(bot.db_path, t["id"])
        dates = sl.format_date_range(t.get("start_date"), t.get("end_date"))
        fmt = FORMAT_LABELS.get(t["format"], t["format"])
        rounds_line = await _rounds_line(bot.db_path, t["id"])
        value = (f"{fmt} • {t['holes']} holes • {t['course']}\n"
                 f"⛳ {sl.format_settings(t)}\n"
                 f"🗓️ {dates}\n👥 {len(roster)} registered")
        if rounds_line:
            value += f"\n{rounds_line}"
        embed.add_field(
            name=f"{t['name']} (ID {t['id']})",
            value=value,
            inline=False,
        )
    return embed, tournaments


async def build_teesheet_board(bot, guild_id):
    """Returns (embed, sections) for the tee sheet board.

    sections is [(tournament, [tee_time, ...]), ...] for the button view.
    """
    rows = await _upcoming_tee_times(bot.db_path, guild_id)
    embed = discord.Embed(title="🕐 Tee Sheet", color=0x2E7D32)
    if not rows:
        embed.description = (
            "No tee times yet. Registered for a tournament? "
            "Create one with `/tee_time create`."
        )
        return embed, []
    embed.description = (
        "Every upcoming tee time, all tournaments. Tap **Request** and the "
        "tee-time creator approves you."
    )
    shown = rows[:MAX_TEETIME_BUTTONS]
    if len(rows) > MAX_TEETIME_BUTTONS:
        embed.set_footer(
            text=f"Showing {MAX_TEETIME_BUTTONS} of {len(rows)} tee times — "
                 "see the rest with /tee_times"
        )
    sections: list[tuple[dict, list[dict]]] = []
    for t, tt, players, pending in shown:
        if not sections or sections[-1][0]["id"] != t["id"]:
            fmt = FORMAT_LABELS.get(t["format"], t["format"])
            dates = sl.format_date_range(t.get("start_date"), t.get("end_date"))
            rounds_line = await _rounds_line(bot.db_path, t["id"])
            value = (f"{fmt} • {t['holes']} holes • {t['course']}\n"
                     f"⛳ {sl.format_settings(t)} • 🗓️ {dates}")
            if rounds_line:
                value += f"\n{rounds_line}"
            embed.add_field(
                name=f"━━ {t['name']} ━━",
                value=value,
                inline=False,
            )
            sections.append((t, []))
        sections[-1][1].append(tt)
        round_tag = f" [R{tt.get('round_number') or 1}]"
        embed.add_field(
            name=f"🕐 {tt['label']}{round_tag} (ID {tt['id']})",
            value=_tee_time_value(tt, players, pending),
            inline=False,
        )
    return embed, sections


# ------------------------------------------------------------------ refresh
def _view_cache(bot) -> dict:
    cache = getattr(bot, "_teesheet_views", None)
    if cache is None:
        cache = bot._teesheet_views = {}
    return cache


def get_view(bot, guild_id: str, kind: str) -> discord.ui.View:
    """Cached persistent view instance for a board; registers it once."""
    cache = _view_cache(bot)
    key = (guild_id, kind)
    view = cache.get(key)
    if view is None:
        view = BoardRegisterView() if kind == REGISTER_KIND else BoardTeeSheetView()
        cache[key] = view
        bot.add_view(view)
    return view


async def refresh_boards(bot, guild_id: str) -> None:
    """Rebuild both board messages in place; re-posts if one was deleted."""
    builders = (
        (REGISTER_KIND, build_register_board),
        (TEESHEET_KIND, build_teesheet_board),
    )
    for kind, builder in builders:
        rec = await db.get_board(bot.db_path, guild_id, kind)
        if not rec:
            continue
        embed, payload = await builder(bot, guild_id)
        view = get_view(bot, guild_id, kind)
        view.rebuild(payload)
        channel = bot.get_channel(int(rec["channel_id"]))
        if channel is None:
            continue
        try:
            msg = await channel.fetch_message(int(rec["message_id"]))
            await msg.edit(embed=embed, view=view)
        except (discord.NotFound, discord.HTTPException):
            msg = await channel.send(embed=embed, view=view)
            await db.set_board(bot.db_path, guild_id, kind,
                               rec["channel_id"], msg.id)


async def post_boards(bot, guild_id: str, channel) -> None:
    """Post fresh board messages in ``channel`` and record their locations."""
    for kind, builder in ((REGISTER_KIND, build_register_board),
                          (TEESHEET_KIND, build_teesheet_board)):
        embed, payload = await builder(bot, guild_id)
        view = get_view(bot, guild_id, kind)
        view.rebuild(payload)
        msg = await channel.send(embed=embed, view=view)
        try:
            await msg.pin()
        except (discord.Forbidden, discord.HTTPException):
            pass
        await db.set_board(bot.db_path, guild_id, kind, channel.id, msg.id)


async def startup(bot, guild_id: str) -> None:
    """(Re)register persistent board views; refresh existing boards.

    If no board has been posted yet and a #tee-sheet channel exists, post
    the boards there automatically.
    """
    get_view(bot, guild_id, REGISTER_KIND)
    get_view(bot, guild_id, TEESHEET_KIND)
    has_register = await db.get_board(bot.db_path, guild_id, REGISTER_KIND)
    has_teesheet = await db.get_board(bot.db_path, guild_id, TEESHEET_KIND)
    if has_register or has_teesheet:
        await refresh_boards(bot, guild_id)
        return
    guild = bot.get_guild(int(guild_id))
    channel = discord.utils.get(guild.text_channels, name="tee-sheet") if guild else None
    if channel is not None:
        await post_boards(bot, guild_id, channel)


async def maybe_refresh(bot, guild_id: str) -> None:
    """Best-effort board refresh — never breaks the caller's flow."""
    try:
        await refresh_boards(bot, guild_id)
    except Exception as e:  # noqa: BLE001 - board must not break commands
        print(f"tee-sheet refresh failed for guild {guild_id}: {e}")

"""Tournament lifecycle: /tournament create|list|start|complete (admin only)."""
from typing import Literal, Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from src import db
from src import config
from src import golfplus_courses
from src import leaderboard_render
from src import scoring_logic as sl
from src import teesheet as ts
from src.cogs.common import (
    active_tournament_autocomplete,
    any_tournament_autocomplete,
    require_admin,
    require_strict_admin,
    resolve_tournament,
)

FORMAT_LABELS = {
    "stroke": "Stroke play",
    "match": "Match play",
    "best_ball": "Best ball",
    "alt_shot": "Alternate shot",
    "scramble": "Scramble",
}


async def course_autocomplete(interaction: discord.Interaction, current: str):
    """Suggest Golf+ courses; free text is still accepted (not validated)."""
    return [
        app_commands.Choice(name=name, value=value)
        for name, value in golfplus_courses.course_choices(current)
    ]


class RegisterView(discord.ui.View):
    """Persistent Register button posted on tournament announcements."""

    def __init__(self, tournament_id: int):
        super().__init__(timeout=None)
        self.tournament_id = tournament_id
        button = discord.ui.Button(
            label="✅ Register",
            style=discord.ButtonStyle.success,
            custom_id=f"treg:{tournament_id}",
        )
        button.callback = self._on_register
        self.add_item(button)

    async def _on_register(self, interaction: discord.Interaction):
        from src.cogs import registration as reg_cog

        tournament_id = int(interaction.data["custom_id"].split(":", 1)[1])
        await reg_cog.do_register(interaction, tournament_id)


def build_tournament_announce_embed(
    *,
    tournament_id: int,
    name: str,
    format: str,
    holes: int,
    course: str,
    start_date: Optional[str],
    end_date: Optional[str],
    description: Optional[str],
    rounds: list,
    green_speed: str,
    pars: Optional[str],
    pars_auto: bool = False,
) -> discord.Embed:
    """Shared announcement embed for bot- and app-created tournaments.

    `rounds` is a list of dicts with tee_position/pin_position/wind_strength
    plus start_date/end_date (one per round); single-round tournaments pass
    one entry.
    """
    fmt_label = FORMAT_LABELS[format]
    embed = discord.Embed(
        title=f"⛳ {name.strip()}",
        description=(description or "").strip()
        or "A new tournament is open for registration!",
        color=0x1B6CA8,
    )
    embed.add_field(name="Format", value=fmt_label, inline=True)
    embed.add_field(name="Holes", value=str(holes), inline=True)
    embed.add_field(name="Course", value=course.strip(), inline=True)
    num_rounds = len(rounds)
    if num_rounds > 1:
        embed.add_field(
            name="Rounds", value=f"🔁 {num_rounds} rounds", inline=True)
    embed.add_field(
        name="Dates",
        value=f"🗓️ {sl.format_date_range(start_date, end_date)}",
        inline=True,
    )
    if num_rounds > 1:
        round_lines = "\n".join(
            f"R{i}: {sl.format_date_range(r.get('start_date'), r.get('end_date'))} • "
            f"{sl.TEE_LABELS[r['tee_position']]} tees • "
            f"{sl.PIN_LABELS[r['pin_position']]} pins • "
            f"{sl.WIND_LABELS[r['wind_strength']]} wind"
            for i, r in enumerate(rounds, start=1)
        )
        embed.add_field(
            name="Round settings",
            value=f"{round_lines}\n🟢 {sl.GREEN_LABELS[green_speed]} greens "
                  f"(all rounds)\n_Tune per round with /tournament set_round._",
            inline=False,
        )
    else:
        r0 = rounds[0] if rounds else {}
        embed.add_field(
            name="Settings",
            value=f"⛳ {sl.TEE_LABELS[r0.get('tee_position', 'middle')]} tees • "
                  f"📍 {sl.PIN_LABELS[r0.get('pin_position', 'white')]} pins • "
                  f"💨 {sl.WIND_LABELS[r0.get('wind_strength', 'moderate')]} wind • "
                  f"🟢 {sl.GREEN_LABELS[green_speed]} greens",
            inline=True,
        )
    if pars:
        par_total = sum(int(p) for p in pars.split(","))
        par_label = f"{par_total} (course pars)" if pars_auto else str(par_total)
        embed.add_field(name="Par", value=par_label, inline=True)
    if config.APP_DOWNLOAD_URL:
        how_to = ("Hit **📲 Get the app** below to download the companion app "
                  "and register there.")
    else:
        how_to = ("Hit **✅ Register** below, then grab a tee time with "
                  "`/tee_time create` or request to join an open one from "
                  "`/tee_times` (the tee-time creator approves requests).")
    embed.add_field(name="How to join", value=how_to, inline=False)
    embed.set_footer(text=f"Tournament ID: {tournament_id}")
    return embed


def announcement_view(tournament_id: int) -> discord.ui.View:
    """Button row for a tournament announcement.

    Discord-native Register button today; when APP_DOWNLOAD_URL is configured
    it becomes an app-download link button instead.
    """
    if config.APP_DOWNLOAD_URL:
        view = discord.ui.View(timeout=None)
        view.add_item(discord.ui.Button(
            label="📲 Get the app to register",
            style=discord.ButtonStyle.link,
            url=config.APP_DOWNLOAD_URL,
        ))
        return view
    return RegisterView(tournament_id)


def signup_channel(bot: commands.Bot, guild_id: str):
    """The #event-signups channel, or None when the guild lacks one."""
    guild = bot.get_guild(int(guild_id))
    if guild is None:
        return None
    return discord.utils.get(guild.text_channels, name="event-signups")


async def post_signup_announcement(
    bot: commands.Bot, guild_id: str, tournament_id: int
) -> bool:
    """Post a tournament's announcement in #event-signups.

    Used for app-created tournaments (via the outbox drain). Returns True
    when the message was posted.
    """
    t = await db.get_tournament(bot.db_path, tournament_id)
    if not t or t["status"] != "registration_open":
        return False
    channel = signup_channel(bot, guild_id)
    if channel is None:
        return False
    rounds = await db.list_rounds(bot.db_path, tournament_id)
    pars = t.get("pars")
    pars_auto = bool(
        pars and golfplus_courses.course_pars(t["course"], t["holes"]))
    embed = build_tournament_announce_embed(
        tournament_id=tournament_id,
        name=t["name"],
        format=t["format"],
        holes=t["holes"],
        course=t["course"],
        start_date=t.get("start_date"),
        end_date=t.get("end_date"),
        description=t.get("description"),
        rounds=rounds or [{
            "tee_position": t.get("tee_position", "middle"),
            "pin_position": t.get("pin_position", "white"),
            "wind_strength": t.get("wind_strength", "moderate"),
        }],
        green_speed=t.get("green_speed", "pro"),
        pars=pars,
        pars_auto=pars_auto,
    )
    await channel.send(embed=embed, view=announcement_view(tournament_id))
    await ts.maybe_refresh(bot, guild_id)
    return True


async def post_final_standings(
    bot: commands.Bot, guild_id: str, tournament_id: int
) -> bool:
    """Post a tournament's final standings in #event-signups.

    Used for app-completed tournaments (via the outbox drain). Returns True
    when the message was posted.
    """
    t = await db.get_tournament(bot.db_path, tournament_id)
    if not t or t["status"] != "completed":
        return False
    channel = signup_channel(bot, guild_id)
    if channel is None:
        return False
    embed = await leaderboard_render.build_leaderboard_embed(
        bot.db_path, tournament_id, final=True)
    await channel.send(
        content=f"🏁 **{t['name']}** is complete! Final standings:",
        embed=embed,
    )
    await ts.maybe_refresh(bot, guild_id)
    return True


class Tournaments(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        # Re-attach persistent Register buttons after a restart.
        for t in await db.list_tournaments_by_status(
            self.bot.db_path, ["registration_open"]
        ):
            self.bot.add_view(RegisterView(t["id"]))
        self._drain_outbox.start()

    async def cog_unload(self):
        self._drain_outbox.cancel()

    @tasks.loop(minutes=1)
    async def _drain_outbox(self):
        """Pick up tournaments created/completed via the companion app API.

        The API can't touch Discord, so it enqueues events here:
        - tournament_created -> announcement in #event-signups, persistent
          Register button, Tee Sheet refresh.
        - tournament_completed -> final standings post in #event-signups.
        - tournament_ended -> Tee Sheet refresh (silent close, no post).
        Idempotent: re-adding an existing view and re-refreshing the board
        are harmless; announcement/standings posts skip tournaments that no
        longer exist or are in the wrong state.
        """
        try:
            rows = await db.poll_outbox(self.bot.db_path)
        except Exception as e:  # noqa: BLE001 - outbox drain is best-effort
            print(f"outbox drain failed: {e}")
            return
        done = []
        for row in rows:
            try:
                if row["kind"] == "tournament_created":
                    tid = int(row["payload"].get("tournament_id", 0))
                    t = await db.get_tournament(self.bot.db_path, tid)
                    if t and t["status"] == "registration_open":
                        self.bot.add_view(RegisterView(tid))
                        posted = await post_signup_announcement(
                            self.bot, t["guild_id"], tid)
                        if not posted:
                            # No #event-signups channel (or lookup failed);
                            # the Tee Sheet board still shows the tournament.
                            await ts.maybe_refresh(self.bot, t["guild_id"])
                elif row["kind"] == "tournament_completed":
                    tid = int(row["payload"].get("tournament_id", 0))
                    guild_id = row["payload"].get("guild_id")
                    if not guild_id:
                        t = await db.get_tournament(self.bot.db_path, tid)
                        guild_id = t["guild_id"] if t else None
                    if guild_id:
                        await post_final_standings(self.bot, guild_id, tid)
                elif row["kind"] == "tournament_ended":
                    guild_id = row["payload"].get("guild_id")
                    if guild_id:
                        await ts.maybe_refresh(self.bot, guild_id)
                done.append(row["id"])
            except Exception as e:  # noqa: BLE001 - one bad row skips, rest drain
                print(f"outbox row {row['id']} failed: {e}")
        if done:
            try:
                await db.ack_outbox(self.bot.db_path, done)
            except Exception as e:  # noqa: BLE001
                print(f"outbox ack failed: {e}")

    @_drain_outbox.before_loop
    async def _drain_outbox_before(self):
        await self.bot.wait_until_ready()

    tournament = app_commands.Group(
        name="tournament", description="Create and manage tournaments"
    )

    @tournament.command(name="create", description="Create a new tournament (admin)")
    @app_commands.autocomplete(course=course_autocomplete)
    @app_commands.describe(
        name="Tournament name, e.g. 'Friday Night Skins'",
        format="Scoring format",
        holes="9 or 18 holes",
        course="Course name in Golf+",
        start_date="Start date as YYYY-MM-DD, e.g. 2026-10-03",
        end_date="End date as YYYY-MM-DD, e.g. 2026-10-10",
        tee_position="Which tees to play from (default: Middle)",
        pin_position="Pin color for the round (default: White)",
        wind_strength="Wind strength for the round (default: Moderate)",
        green_speed="Green speed: Very Fast or Pro (default: Pro)",
        rounds="Number of 18-hole rounds, 1-5 (default: 1). Every round uses the same course; each round copies these tee/pin/wind settings (tune per round later with /tournament set_round or the app)",
        pars="Optional — auto-fills from the course database when you pick a known course; enter comma-separated pars to override",
        description="Optional blurb shown on the announcement",
    )
    async def tournament_create(
        self,
        interaction: discord.Interaction,
        name: str,
        format: Literal["stroke", "match", "best_ball", "alt_shot", "scramble"],
        holes: Literal[9, 18],
        course: str,
        start_date: str,
        end_date: str,
        # Round settings are optional with sensible defaults so older cached
        # command definitions (missing these options) still submit cleanly.
        tee_position: Literal["front", "middle", "back"] = "middle",
        pin_position: Literal["black", "white", "red"] = "white",
        wind_strength: Literal["low", "moderate", "severe"] = "moderate",
        green_speed: Literal["veryfast", "pro"] = "pro",
        rounds: Literal[1, 2, 3, 4, 5] = 1,
        pars: Optional[str] = None,
        description: Optional[str] = None,
    ):
        if not await require_admin(interaction):
            return
        await interaction.response.defer(ephemeral=True)

        if format == "match" and rounds > 1:
            await interaction.followup.send(
                "❌ Match play tournaments are single-round.", ephemeral=True
            )
            return
        if rounds > 1 and holes != 18:
            await interaction.followup.send(
                "❌ Multi-round tournaments are 18 holes per round.",
                ephemeral=True,
            )
            return

        try:
            start_d, end_d = sl.validate_date_range(start_date, end_date)
        except ValueError as e:
            await interaction.followup.send(f"❌ {e}", ephemeral=True)
            return

        pars_clean = None
        pars_auto = False
        if pars:
            try:
                parsed = sl.parse_pars(pars, holes)
            except ValueError as e:
                await interaction.followup.send(f"❌ Bad pars: {e}", ephemeral=True)
                return
            pars_clean = ",".join(str(p) for p in parsed)
        else:
            auto = golfplus_courses.course_pars(course.strip(), holes)
            if auto:
                pars_clean = ",".join(str(p) for p in auto)
                pars_auto = True

        tid = await db.create_tournament(
            self.bot.db_path,
            str(interaction.guild_id),
            name.strip()[:80],
            format,
            holes,
            course.strip()[:80],
            pars_clean,
            (description or "").strip()[:500] or None,
            str(interaction.user.id),
            start_date=start_d.isoformat(),
            end_date=end_d.isoformat(),
            tee_position=tee_position,
            pin_position=pin_position,
            wind_strength=wind_strength,
            green_speed=green_speed,
            rounds=[
                {"tee_position": tee_position,
                 "pin_position": pin_position,
                 "wind_strength": wind_strength}
                for _ in range(rounds)
            ],
        )
        self.bot.add_view(RegisterView(tid))

        # Real rounds carry the default-split dates from create_tournament.
        created_rounds = await db.list_rounds(self.bot.db_path, tid)
        embed = build_tournament_announce_embed(
            tournament_id=tid,
            name=name,
            format=format,
            holes=holes,
            course=course,
            start_date=start_d.isoformat(),
            end_date=end_d.isoformat(),
            description=description,
            rounds=created_rounds,
            green_speed=green_speed,
            pars=pars_clean,
            pars_auto=pars_auto,
        )
        channel = signup_channel(self.bot, str(interaction.guild_id))
        view = announcement_view(tid)
        if channel is not None:
            await channel.send(embed=embed, view=view)
            where = "announced in #event-signups"
        else:
            # No #event-signups channel - fall back to the invoking channel.
            await interaction.channel.send(embed=embed, view=view)
            where = "announced here (#event-signups not found)"
        await ts.maybe_refresh(self.bot, str(interaction.guild_id))
        await interaction.followup.send(
            f"\u2705 **{name.strip()}** created - {where}.", ephemeral=True
        )

    @tournament.command(name="set_round",
                        description="Change a round's settings or dates (admin)")
    @app_commands.autocomplete(tournament=active_tournament_autocomplete)
    @app_commands.describe(
        tournament="Defaults to the single active tournament",
        round_number="Which round (1-5) to change",
        tee_position="Which tees to play from",
        pin_position="Pin color for the round",
        wind_strength="Wind strength for the round",
        start_date="Round start as YYYY-MM-DD (must be inside the tournament's dates)",
        end_date="Round end as YYYY-MM-DD (must be inside the tournament's dates)",
    )
    async def tournament_set_round(
        self,
        interaction: discord.Interaction,
        round_number: Literal[1, 2, 3, 4, 5],
        tournament: Optional[int] = None,
        tee_position: Optional[Literal["front", "middle", "back"]] = None,
        pin_position: Optional[Literal["black", "white", "red"]] = None,
        wind_strength: Optional[Literal["low", "moderate", "severe"]] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ):
        if not await require_admin(interaction):
            return
        t, err = await resolve_tournament(
            interaction, tournament, ["registration_open", "in_progress"]
        )
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        try:
            r_start, r_end = sl.validate_round_dates(
                start_date, end_date, t.get("start_date"), t.get("end_date"))
        except ValueError as e:
            await interaction.response.send_message(f"❌ {e}", ephemeral=True)
            return
        rnd = await db.update_round(
            self.bot.db_path, t["id"], round_number,
            tee_position=tee_position, pin_position=pin_position,
            wind_strength=wind_strength,
            start_date=r_start, end_date=r_end,
        )
        if rnd is None:
            await interaction.response.send_message(
                f"❌ Round {round_number} doesn't exist for **{t['name']}**.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            f"✅ **{t['name']}** — Round {round_number}: "
            f"🗓️ {sl.format_date_range(rnd.get('start_date'), rnd.get('end_date'))} • "
            f"{sl.TEE_LABELS[rnd['tee_position']]} tees • "
            f"{sl.PIN_LABELS[rnd['pin_position']]} pins • "
            f"{sl.WIND_LABELS[rnd['wind_strength']]} wind.",
            ephemeral=True,
        )
        await ts.maybe_refresh(self.bot, str(interaction.guild_id))

    @tournament.command(name="list", description="List tournaments in this server")
    async def tournament_list(self, interaction: discord.Interaction):
        rows = await db.list_tournaments(self.bot.db_path, str(interaction.guild_id))
        if not rows:
            await interaction.response.send_message(
                "No tournaments yet. An admin can create one with `/tournament create`.",
                ephemeral=True,
            )
            return
        embed = discord.Embed(title="⛳ Tournaments", color=0x1B6CA8)
        embed.description = (
            "Pick the event(s) you want — play in none, some, or all of them.\n"
            "Register, then grab a tee time with `/tee_time create` or request "
            "to join one from `/tee_times`."
        )
        for t in rows[:25]:
            status = t["status"].replace("_", " ")
            fmt_label = FORMAT_LABELS.get(t["format"], t["format"])
            dates = sl.format_date_range(t.get("start_date"), t.get("end_date"))
            rounds = await db.list_rounds(self.bot.db_path, t["id"])
            if len(rounds) > 1:
                round_bits = " • ".join(
                    f"R{i}: {sl.format_date_range(r.get('start_date'), r.get('end_date'))}"
                    for i, r in enumerate(rounds, start=1)
                )
                settings_line = (f"🔁 {len(rounds)} rounds • ⛳ {sl.format_settings(t)}\n"
                                 f"{round_bits}")
            else:
                settings_line = f"⛳ {sl.format_settings(t)}"
            embed.add_field(
                name=f"{t['name']} (ID {t['id']})",
                value=(f"{fmt_label} • {t['holes']} holes • {t['course']}\n"
                       f"{settings_line}\n"
                       f"🗓️ {dates}\nStatus: **{status}**"),
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @tournament.command(name="start", description="Start a tournament & post the live leaderboard (admin)")
    @app_commands.autocomplete(tournament=active_tournament_autocomplete)
    @app_commands.describe(tournament="Defaults to the single active tournament")
    async def tournament_start(
        self, interaction: discord.Interaction, tournament: Optional[int] = None
    ):
        if not await require_admin(interaction):
            return
        t, err = await resolve_tournament(
            interaction, tournament, ["registration_open", "in_progress"]
        )
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        if t["status"] == "in_progress":
            await interaction.response.send_message(
                f"**{t['name']}** is already in progress.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        await db.set_tournament_status(self.bot.db_path, t["id"], "in_progress")
        embed = await leaderboard_render.build_leaderboard_embed(
            self.bot.db_path, t["id"]
        )
        msg = await interaction.channel.send(
            content="🟢 **Tournament is live!** Good luck, everyone.",
            embed=embed,
        )
        try:
            await msg.pin()
        except (discord.Forbidden, discord.HTTPException):
            pass  # board still works unpinned
        await db.set_leaderboard_message(
            self.bot.db_path, t["id"], interaction.channel_id, msg.id
        )
        await ts.maybe_refresh(self.bot, str(interaction.guild_id))
        await interaction.followup.send(
            f"✅ **{t['name']}** is now in progress. The leaderboard will update "
            "automatically as scores come in.",
            ephemeral=True,
        )

    @tournament.command(name="complete", description="Finish a tournament & post final standings (admin)")
    @app_commands.autocomplete(tournament=active_tournament_autocomplete)
    @app_commands.describe(tournament="Defaults to the single active tournament")
    async def tournament_complete(
        self, interaction: discord.Interaction, tournament: Optional[int] = None
    ):
        if not await require_admin(interaction):
            return
        t, err = await resolve_tournament(
            interaction, tournament, ["registration_open", "in_progress"]
        )
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await db.set_tournament_status(self.bot.db_path, t["id"], "completed")
        # Stale join requests can't linger past the end of the event.
        expired = await db.expire_join_requests_for_tournament(
            self.bot.db_path, t["id"]
        )
        embed = await leaderboard_render.build_leaderboard_embed(
            self.bot.db_path, t["id"], final=True
        )
        await interaction.channel.send(
            content=f"🏁 **{t['name']}** is complete! Final standings:",
            embed=embed,
        )
        # Season points: award to every active season containing this tournament.
        season_msg = ""
        try:
            points_rows = await leaderboard_render.final_standings_points(
                self.bot.db_path, t["id"]
            )
            seasons = await db.get_seasons_for_tournament(
                self.bot.db_path, t["id"], "active"
            )
            awarded = 0
            for s in seasons:
                awarded += await db.record_season_points(
                    self.bot.db_path, s["id"], t["id"],
                    [(r["player_discord_id"], r["position"], r["points"])
                     for r in points_rows],
                )
            if seasons:
                names = ", ".join(f"**{s['name']}**" for s in seasons)
                season_msg = (f" Season points awarded to {awarded} player(s)"
                              f" in {names}.")
        except Exception as e:  # never break /tournament complete
            print(f"season points award failed for tournament {t['id']}: {e}")
            season_msg = " (⚠️ season points could not be awarded — check the logs)"
        note = f" ({expired} pending join request(s) expired)" if expired else ""
        await ts.maybe_refresh(self.bot, str(interaction.guild_id))
        await interaction.followup.send(
            f"✅ **{t['name']}** marked complete.{note}{season_msg}", ephemeral=True
        )

    @tournament.command(name="edit", description="Edit a tournament's details (admin)")
    @app_commands.autocomplete(tournament=any_tournament_autocomplete)
    @app_commands.describe(
        tournament="Which tournament to edit",
        name="New name (leave blank to keep)",
        description="New blurb (leave blank to keep)",
        start_date="New start date as YYYY-MM-DD (leave blank to keep)",
        end_date="New end date as YYYY-MM-DD (leave blank to keep)",
        course="New course (leave blank to keep)",
    )
    async def tournament_edit(
        self,
        interaction: discord.Interaction,
        tournament: Optional[int] = None,
        name: Optional[str] = None,
        description: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        course: Optional[str] = None,
    ):
        if not await require_admin(interaction):
            return
        t, err = await resolve_tournament(
            interaction, tournament,
            ["registration_open", "in_progress", "completed"])
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        if all(v is None for v in (name, description, start_date, end_date,
                                   course)):
            await interaction.response.send_message(
                "❌ Nothing to change — give me a new `name`, `description`, "
                "`start_date`, `end_date` or `course`.", ephemeral=True)
            return
        new_start = start_date or t.get("start_date")
        new_end = end_date or t.get("end_date")
        if start_date or end_date:
            try:
                sl.validate_date_range(new_start, new_end)
            except ValueError as e:
                await interaction.response.send_message(
                    f"❌ {e}", ephemeral=True)
                return
        changed = await db.update_tournament(
            self.bot.db_path, t["id"],
            name=name.strip()[:80] if name and name.strip() else None,
            description=description.strip()[:500] if description else None,
            start_date=new_start if start_date else None,
            end_date=new_end if end_date else None,
            course=course.strip()[:80] if course and course.strip() else None,
        )
        if not changed:
            await interaction.response.send_message(
                "Nothing changed.", ephemeral=True)
            return
        await ts.maybe_refresh(self.bot, str(interaction.guild_id))
        await interaction.response.send_message(
            f"✅ **{name.strip()[:80] if name and name.strip() else t['name']}** "
            f"updated.", ephemeral=True)

    @tournament.command(name="end",
                        description="End a tournament now, no final standings (admin)")
    @app_commands.autocomplete(tournament=active_tournament_autocomplete)
    @app_commands.describe(tournament="Defaults to the single active tournament")
    async def tournament_end(
        self, interaction: discord.Interaction,
        tournament: Optional[int] = None,
    ):
        """Close a tournament immediately without final standings or season
        points — for events that fizzle out. /tournament complete is the
        full finish with standings and points."""
        if not await require_admin(interaction):
            return
        t, err = await resolve_tournament(
            interaction, tournament, ["registration_open", "in_progress"])
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return

        class ConfirmEnd(discord.ui.View):
            def __init__(self, cog: "Tournaments"):
                super().__init__(timeout=60)
                self.cog = cog

            @discord.ui.button(label="End it", style=discord.ButtonStyle.danger)
            async def confirm(self, btn_interaction: discord.Interaction,
                              button: discord.ui.Button):
                if not await require_admin(btn_interaction):
                    return
                t2 = await db.get_tournament(
                    btn_interaction.client.db_path, t["id"])
                if not t2 or t2["status"] == "completed":
                    await btn_interaction.response.edit_message(
                        content="Already ended.", view=None)
                    return
                await db.set_tournament_status(
                    btn_interaction.client.db_path, t["id"], "completed")
                await db.expire_join_requests_for_tournament(
                    btn_interaction.client.db_path, t["id"])
                await ts.maybe_refresh(
                    btn_interaction.client, str(btn_interaction.guild_id))
                await btn_interaction.response.edit_message(
                    content=f"🏁 **{t['name']}** ended (no final standings "
                            f"posted, no season points).", view=None)

            @discord.ui.button(label="Keep it going",
                               style=discord.ButtonStyle.secondary)
            async def cancel(self, btn_interaction: discord.Interaction,
                             button: discord.ui.Button):
                await btn_interaction.response.edit_message(
                    content="Kept — tournament still running.", view=None)

        await interaction.response.send_message(
            f"End **{t['name']}** now? It'll be marked complete with no final "
            f"standings and no season points. (Use `/tournament complete` for "
            f"the full finish.)",
            view=ConfirmEnd(self),
            ephemeral=True,
        )

    @tournament.command(name="delete",
                        description="Permanently delete a tournament (admins only)")
    @app_commands.autocomplete(tournament=any_tournament_autocomplete)
    @app_commands.describe(tournament="Which tournament to delete")
    async def tournament_delete(
        self, interaction: discord.Interaction,
        tournament: Optional[int] = None,
    ):
        if not await require_strict_admin(interaction):
            return
        t, err = await resolve_tournament(
            interaction, tournament,
            ["registration_open", "in_progress", "completed"])
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        counts = await db.tournament_usage_counts(self.bot.db_path, t["id"])

        class ConfirmDelete(discord.ui.View):
            def __init__(self, cog: "Tournaments"):
                super().__init__(timeout=60)
                self.cog = cog

            @discord.ui.button(label="Delete forever",
                               style=discord.ButtonStyle.danger)
            async def confirm(self, btn_interaction: discord.Interaction,
                              button: discord.ui.Button):
                if not await require_strict_admin(btn_interaction):
                    return
                t2 = await db.get_tournament(
                    btn_interaction.client.db_path, t["id"])
                if not t2:
                    await btn_interaction.response.edit_message(
                        content="Already deleted.", view=None)
                    return
                await db.delete_tournament(
                    btn_interaction.client.db_path, t["id"])
                await ts.maybe_refresh(
                    btn_interaction.client, str(btn_interaction.guild_id))
                await btn_interaction.response.edit_message(
                    content=f"🗑️ Tournament **{t['name']}** deleted — "
                            f"registrations, tee times and scores are gone.",
                    view=None)

            @discord.ui.button(label="Keep it",
                               style=discord.ButtonStyle.secondary)
            async def cancel(self, btn_interaction: discord.Interaction,
                             button: discord.ui.Button):
                await btn_interaction.response.edit_message(
                    content="Kept — nothing deleted.", view=None)

        await interaction.response.send_message(
            f"⚠️ Permanently delete **{t['name']}**? This removes "
            f"{counts['registrations']} registration(s), "
            f"{counts['tee_times']} tee time(s) and "
            f"{counts['scorecards']} scorecard(s). This can't be undone.",
            view=ConfirmDelete(self),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Tournaments(bot))

"""Tournament lifecycle: /tournament create|list|start|complete (admin only)."""
from typing import Literal, Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from src import db
from src import golfplus_courses
from src import leaderboard_render
from src import scoring_logic as sl
from src import teesheet as ts
from src.cogs.common import (
    active_tournament_autocomplete,
    require_admin,
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
        """Pick up tournaments created via the companion app API.

        The API can't touch Discord, so it enqueues a tournament_created
        event; here we attach the persistent Register button and refresh
        the Tee Sheet board. Idempotent: re-adding an existing view and
        re-refreshing the board are harmless.
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
                        await ts.maybe_refresh(self.bot, t["guild_id"])
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

        fmt_label = FORMAT_LABELS[format]
        embed = discord.Embed(
            title=f"⛳ {name.strip()}",
            description=(description or "").strip() or "A new tournament is open for registration!",
            color=0x1B6CA8,
        )
        embed.add_field(name="Format", value=fmt_label, inline=True)
        embed.add_field(name="Holes", value=str(holes), inline=True)
        embed.add_field(name="Course", value=course.strip(), inline=True)
        if rounds > 1:
            embed.add_field(name="Rounds", value=f"🔁 {rounds} rounds",
                            inline=True)
        embed.add_field(
            name="Dates",
            value=f"🗓️ {sl.format_date_range(start_d.isoformat(), end_d.isoformat())}",
            inline=True,
        )
        if rounds > 1:
            round_lines = "\n".join(
                f"R{i}: {sl.TEE_LABELS[tee_position]} tees • "
                f"{sl.PIN_LABELS[pin_position]} pins • "
                f"{sl.WIND_LABELS[wind_strength]} wind"
                for i in range(1, rounds + 1)
            )
            embed.add_field(
                name="Round settings",
                value=f"{round_lines}\n🟢 {sl.GREEN_LABELS[green_speed]} greens "
                      f"(all rounds)\n_Tune per round with /tournament set_round._",
                inline=False,
            )
        else:
            embed.add_field(
                name="Settings",
                value=f"⛳ {sl.TEE_LABELS[tee_position]} tees • "
                      f"📍 {sl.PIN_LABELS[pin_position]} pins • "
                      f"💨 {sl.WIND_LABELS[wind_strength]} wind • "
                      f"🟢 {sl.GREEN_LABELS[green_speed]} greens",
                inline=True,
            )
        if pars_clean:
            par_total = sum(int(p) for p in pars_clean.split(","))
            par_label = f"{par_total} (course pars)" if pars_auto else str(par_total)
            embed.add_field(name="Par", value=par_label, inline=True)
        embed.add_field(
            name="How to join",
            value="Hit **✅ Register** below, then grab a tee time with `/tee_time create` "
                  "or request to join an open one from `/tee_times` (the tee-time "
                  "creator approves requests).",
            inline=False,
        )
        embed.set_footer(text=f"Tournament ID: {tid}")
        await interaction.channel.send(embed=embed, view=RegisterView(tid))
        await ts.maybe_refresh(self.bot, str(interaction.guild_id))
        await interaction.followup.send(
            f"✅ Tournament **{name.strip()}** created and announced.", ephemeral=True
        )

    @tournament.command(name="set_round",
                        description="Change a round's tee/pin/wind (admin)")
    @app_commands.autocomplete(tournament=active_tournament_autocomplete)
    @app_commands.describe(
        tournament="Defaults to the single active tournament",
        round_number="Which round (1-5) to change",
        tee_position="Which tees to play from",
        pin_position="Pin color for the round",
        wind_strength="Wind strength for the round",
    )
    async def tournament_set_round(
        self,
        interaction: discord.Interaction,
        round_number: Literal[1, 2, 3, 4, 5],
        tournament: Optional[int] = None,
        tee_position: Optional[Literal["front", "middle", "back"]] = None,
        pin_position: Optional[Literal["black", "white", "red"]] = None,
        wind_strength: Optional[Literal["low", "moderate", "severe"]] = None,
    ):
        if not await require_admin(interaction):
            return
        t, err = await resolve_tournament(
            interaction, tournament, ["registration_open", "in_progress"]
        )
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        rnd = await db.update_round(
            self.bot.db_path, t["id"], round_number,
            tee_position=tee_position, pin_position=pin_position,
            wind_strength=wind_strength,
        )
        if rnd is None:
            await interaction.response.send_message(
                f"❌ Round {round_number} doesn't exist for **{t['name']}**.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            f"✅ **{t['name']}** — Round {round_number}: "
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
                settings_line = f"🔁 {len(rounds)} rounds • ⛳ {sl.format_settings(t)}"
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


async def setup(bot: commands.Bot):
    await bot.add_cog(Tournaments(bot))

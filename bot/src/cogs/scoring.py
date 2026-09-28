"""Scoring: /submit_score (tap-to-enter view), /my_score, /scorecard, /team,
admin tools.

Verification rule: a scorecard auto-verifies when the player's tee time has
>= 2 players (playing partners present). Solo rounds stay PENDING until an
admin verifies them with /verify_score.
"""
import json
from typing import Literal, Optional

import discord
from discord import app_commands
from discord.ext import commands

from src import db
from src import leaderboard_render
from src import scoring_logic as sl
from src.cogs.common import (
    active_tournament_autocomplete,
    admin_role_mention,
    inprogress_tournament_autocomplete,
    is_admin,
    require_admin,
    resolve_tournament,
    viewer_tee_time_when,
)

TEAM_FORMATS = ("best_ball", "alt_shot", "scramble")
# Formats where the team submits ONE shared scorecard (vs. best ball, which
# aggregates each member's individual card).
SHARED_CARD_FORMATS = ("alt_shot", "scramble")


async def _tee_time_choices(interaction: discord.Interaction, current: str):
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


async def _locked_message(tt: dict) -> str:
    """Score-entry gate message with a relative Discord timestamp."""
    unix = sl.tee_time_unix(tt.get("starts_at") or "")
    when = f"<t:{unix}:R>" if unix else "your tee time"
    return f"⏳ Your tee time is {when} — score entry unlocks after your round."


class CustomScoreModal(discord.ui.Modal):
    """One-off numeric entry for the current hole (hole-in-one on a par 4/5,
    blowups beyond par+3)."""

    def __init__(self, view: "ScoreEntryView"):
        super().__init__(title=f"Hole {view.idx + 1} — custom score")
        self._view = view
        self.score_input = discord.ui.TextInput(
            label="Score (1-15)",
            style=discord.TextStyle.short,
            placeholder="e.g. 8",
            required=True,
            max_length=2,
        )
        self.add_item(self.score_input)

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.score_input.value.strip()
        if not raw.isdigit() or not 1 <= int(raw) <= 15:
            await interaction.response.send_message(
                "❌ Score must be a whole number from 1-15.", ephemeral=True
            )
            return
        await interaction.response.defer()
        try:
            await self._view.record_score(int(raw))
            await self._view.refresh_message()
        except Exception:
            await interaction.followup.send(
                "❌ Couldn't record that score — try again.", ephemeral=True
            )


class ScoreEntryView(discord.ui.View):
    """Tap-to-enter scorecard.

    Anyone in the tee time can enter any one player's card (their own or a
    playing partner's) via the player picker at the top. Score buttons show
    real numbers (par-2 … par+3); Prev/Next move between holes; the custom
    button covers anything outside the button range.
    """

    def __init__(self, bot: commands.Bot, t: dict, tt: dict,
                 players: list[dict], teams: list[dict], submitter_id: str,
                 rounds: list[dict] | None = None):
        super().__init__(timeout=1800)  # 30 minutes to finish the card
        self.bot = bot
        self.db_path = bot.db_path
        self.t = t
        self.tt = tt
        self.players = players
        self.rounds = rounds or []
        self.round_number = 1
        self.round_select: discord.ui.Select | None = None
        self.holes = t["holes"]
        try:
            pars = [int(x) for x in t["pars"].split(",")] if t.get("pars") else None
            self.pars = pars if pars and len(pars) == self.holes else None
        except (ValueError, AttributeError):
            self.pars = None
        self.team_required = t["format"] in TEAM_FORMATS
        self.submit_id = submitter_id
        self.card_owner_id = submitter_id  # player picker defaults to submitter
        self.teams = teams  # selected player's teams in this tournament
        self.team_id: str | None = None
        self.scores: list[int | None] = [None] * self.holes
        self.idx = 0
        self.player_select: discord.ui.Select | None = None
        self.team_select: discord.ui.Select | None = None
        self._build_items()

    # ------------------------------ rendering ------------------------------
    def _name(self, discord_id: str) -> str:
        for p in self.players:
            if p["discord_id"] == str(discord_id):
                return p["display_name"]
        return str(discord_id)

    def _par(self) -> int:
        return self.pars[self.idx] if self.pars else 4

    def render(self) -> discord.Embed:
        par = self._par()
        cur = self.scores[self.idx]
        embed = discord.Embed(
            title=f"⛳ {self.t['name'][:40]} — Submit score",
            color=0x1B6CA8,
        )
        if len(self.rounds) > 1:
            rnd = next((r for r in self.rounds
                        if r["round_number"] == self.round_number), None)
            rlabel = (f"**Round {self.round_number}**"
                      + (f" ({self._round_label(rnd).split(' — ', 1)[1]})"
                         if rnd else ""))
            embed.add_field(name="Round", value=rlabel, inline=True)
        embed.add_field(name="Card for",
                        value=f"**{self._name(self.card_owner_id)}**", inline=True)
        embed.add_field(name="Hole",
                        value=f"**{self.idx + 1}** of {self.holes} • Par {par}",
                        inline=True)
        embed.add_field(name="This hole",
                        value=f"**{cur}**" if cur else "–", inline=True)
        entered = [(i, s) for i, s in enumerate(self.scores) if s]
        if entered:
            total = sum(s for _, s in entered)
            val = f"**{total}**"
            if self.pars:
                diff = total - sum(self.pars[i] for i, _ in entered)
                to_par = "E" if diff == 0 else f"{diff:+d}"
                val += f" ({to_par})"
            embed.add_field(
                name=f"Running total ({len(entered)}/{self.holes})",
                value=val, inline=False,
            )
        prog = " ".join(
            f"[{i + 1}:{s or '–'}]" if i == self.idx else f"{i + 1}:{s or '–'}"
            for i, s in enumerate(self.scores)
        )
        embed.add_field(name="Progress", value=prog, inline=False)
        if self.team_required:
            team_name = next(
                (tm["name"] for tm in self.teams
                 if str(tm["id"]) == str(self.team_id)),
                None,
            )
            embed.add_field(
                name="Team",
                value=f"**{team_name}**" if team_name else "— pick a team above —",
                inline=False,
            )
        return embed

    async def refresh_message(self):
        """Re-render the view's message (used after modal-driven edits)."""
        if self.message:
            self._build_items()
            await self.message.edit(embed=self.render(), view=self)

    async def _fail(self, interaction: discord.Interaction, msg: str):
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except discord.HTTPException:
            pass

    # ------------------------------ item building ------------------------------
    def _round_label(self, r: dict) -> str:
        tee = sl.TEE_LABELS.get(r["tee_position"], r["tee_position"])
        pin = sl.PIN_LABELS.get(r["pin_position"], r["pin_position"])
        wind = sl.WIND_LABELS.get(r["wind_strength"], r["wind_strength"])
        return f"Round {r['round_number']} — {tee} tees, {pin} pins, {wind} wind"

    def _build_items(self):
        self.clear_items()
        row = 0
        if len(self.rounds) > 1:
            ropts = [
                discord.SelectOption(
                    label=self._round_label(r)[:100],
                    value=str(r["round_number"]),
                    default=(r["round_number"] == self.round_number),
                )
                for r in self.rounds
            ]
            rsel = discord.ui.Select(
                placeholder="Which round is this card for?",
                options=ropts, min_values=1, max_values=1, row=0,
            )
            rsel.callback = self._on_pick_round
            self.add_item(rsel)
            self.round_select = rsel
            row = 1
        opts = [
            discord.SelectOption(
                label=p["display_name"][:100],
                value=p["discord_id"],
                default=(p["discord_id"] == self.card_owner_id),
            )
            for p in self.players
        ]
        sel = discord.ui.Select(
            placeholder="Whose score are you entering?",
            options=opts, min_values=1, max_values=1, row=row,
        )
        sel.callback = self._on_pick_player
        self.add_item(sel)
        self.player_select = sel

        btn_row = row + 1
        if self.team_required:
            topts = [
                discord.SelectOption(
                    label=tm["name"][:100],
                    value=str(tm["id"]),
                    default=(str(tm["id"]) == str(self.team_id)),
                )
                for tm in self.teams
            ]
            tsel = discord.ui.Select(
                placeholder="Choose the team…",
                options=topts, min_values=1, max_values=1, row=row + 1,
            )
            tsel.callback = self._on_pick_team
            self.add_item(tsel)
            self.team_select = tsel
            btn_row = row + 2

        par = self._par()
        cur = self.scores[self.idx]
        for n, s in enumerate(sl.score_button_scores(par)):
            b = discord.ui.Button(
                label=str(s),
                style=(discord.ButtonStyle.success
                       if cur == s else discord.ButtonStyle.secondary),
                row=btn_row if n < 5 else btn_row + 1,
            )
            b.callback = self._make_score_cb(s)
            self.add_item(b)

        nav_row = btn_row + 1
        prev = discord.ui.Button(label="◀ Prev",
                                 style=discord.ButtonStyle.secondary, row=nav_row)
        prev.callback = self._on_prev
        nxt = discord.ui.Button(label="Next ▶",
                                style=discord.ButtonStyle.secondary, row=nav_row)
        nxt.callback = self._on_next
        custom = discord.ui.Button(label="🔢 Custom",
                                   style=discord.ButtonStyle.secondary, row=nav_row)
        custom.callback = self._on_custom
        complete = all(self.scores) and (not self.team_required or self.team_id)
        submit = discord.ui.Button(
            label="✅ Submit",
            style=discord.ButtonStyle.success,
            disabled=not complete,
            row=nav_row,
        )
        submit.callback = self._on_submit
        for b in (prev, nxt, custom, submit):
            self.add_item(b)

    # ------------------------------ interactions ------------------------------
    def _make_score_cb(self, score: int):
        async def _cb(interaction: discord.Interaction):
            try:
                await self.record_score(score)
                self._build_items()
                await interaction.response.edit_message(
                    embed=self.render(), view=self
                )
            except Exception:
                await self._fail(interaction,
                                 "❌ Couldn't record that score — try again.")
        return _cb

    async def record_score(self, score: int):
        """Record a score for the current hole, then auto-advance."""
        self.scores[self.idx] = score
        nxt = next((i for i in range(self.holes) if self.scores[i] is None), None)
        self.idx = nxt if nxt is not None else (self.idx + 1) % self.holes

    async def _on_prev(self, interaction: discord.Interaction):
        try:
            self.idx = (self.idx - 1) % self.holes
            self._build_items()
            await interaction.response.edit_message(embed=self.render(), view=self)
        except Exception:
            await self._fail(interaction, "❌ Couldn't move — try again.")

    async def _on_next(self, interaction: discord.Interaction):
        try:
            self.idx = (self.idx + 1) % self.holes
            self._build_items()
            await interaction.response.edit_message(embed=self.render(), view=self)
        except Exception:
            await self._fail(interaction, "❌ Couldn't move — try again.")

    async def _on_custom(self, interaction: discord.Interaction):
        try:
            await interaction.response.send_modal(CustomScoreModal(self))
        except Exception:
            await self._fail(interaction, "❌ Couldn't open custom entry — try again.")

    async def _on_pick_round(self, interaction: discord.Interaction):
        try:
            new_round = int(self.round_select.values[0])
            if new_round == self.round_number:
                await interaction.response.defer()
                return
            # A different round is a different card: reset entry state.
            self.round_number = new_round
            self.scores = [None] * self.holes
            self.idx = 0
            self._build_items()
            await interaction.response.edit_message(embed=self.render(), view=self)
        except Exception:
            await self._fail(interaction, "❌ Couldn't switch rounds — try again.")

    async def _on_pick_player(self, interaction: discord.Interaction):
        try:
            new_owner = self.player_select.values[0]
            if new_owner == self.card_owner_id:
                await interaction.response.defer()
                return
            teams = await db.get_player_teams(
                self.db_path, self.t["id"], new_owner
            )
            if self.team_required and not teams:
                await interaction.response.send_message(
                    f"❌ **{self._name(new_owner)}** isn't on a team in "
                    f"**{self.t['name']}** — create one with `/team create` first.",
                    ephemeral=True,
                )
                return
            # New card: reset the entry state for the newly selected player.
            self.card_owner_id = new_owner
            self.teams = teams
            self.team_id = None
            self.scores = [None] * self.holes
            self.idx = 0
            self._build_items()
            await interaction.response.edit_message(embed=self.render(), view=self)
        except Exception:
            await self._fail(interaction, "❌ Couldn't switch players — try again.")

    async def _on_pick_team(self, interaction: discord.Interaction):
        try:
            self.team_id = self.team_select.values[0]
            self._build_items()
            await interaction.response.edit_message(embed=self.render(), view=self)
        except Exception:
            await self._fail(interaction, "❌ Couldn't pick that team — try again.")

    async def _on_submit(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=True)
            await _save_scorecard(
                self.bot, interaction, self.t["id"], self.tt["id"],
                self.card_owner_id, self.submit_id, self.team_id,
                [s for s in self.scores if s is not None],
                round_number=self.round_number,
            )
        except Exception:
            await self._fail(interaction,
                             "❌ Couldn't submit the score — try again.")


async def _save_scorecard(bot: commands.Bot, interaction: discord.Interaction,
                          t_id: int, tt_id: int, card_owner_id: str,
                          submitter_id: str, team_id: str | None,
                          scores: list[int], round_number: int = 1):
    """Shared save path for score entry.

    The SUBMITTER must be in the tee time; the card is saved under the
    SELECTED player, who must be registered and in the tee time.
    """
    db_path = bot.db_path
    t = await db.get_tournament(db_path, t_id)
    tt = await db.get_tee_time(db_path, tt_id)
    if not t or not tt or t["status"] != "in_progress":
        await interaction.followup.send(
            "❌ This tournament/tee time is no longer accepting scores.",
            ephemeral=True,
        )
        return
    # Defense in depth: the tee-time gate is also enforced on /submit_score.
    if not sl.tee_time_passed(tt.get("starts_at") or ""):
        await interaction.followup.send(await _locked_message(tt), ephemeral=True)
        return
    if not await db.get_round(db_path, t["id"], round_number):
        await interaction.followup.send(
            f"❌ Round {round_number} doesn't exist in **{t['name']}**.",
            ephemeral=True,
        )
        return
    mine = await db.get_player_tee_time(db_path, t["id"], submitter_id)
    if not mine or mine["id"] != tt["id"]:
        await interaction.followup.send(
            f"❌ You're not in the **{tt['label']}** tee time.", ephemeral=True
        )
        return
    if not await db.is_registered(db_path, t["id"], card_owner_id):
        await interaction.followup.send(
            "❌ The selected player isn't registered for "
            f"**{t['name']}**.", ephemeral=True
        )
        return
    owner_tt = await db.get_player_tee_time(db_path, t["id"], card_owner_id)
    if not owner_tt or owner_tt["id"] != tt["id"]:
        await interaction.followup.send(
            "❌ The selected player isn't in this tee time.", ephemeral=True
        )
        return

    team_name = ""
    card_player_id: str | None = card_owner_id
    if t["format"] in TEAM_FORMATS:
        teams = await db.get_player_teams(db_path, t["id"], card_owner_id)
        team = next((tm for tm in teams if str(tm["id"]) == str(team_id)), None)
        if not team:
            await interaction.followup.send(
                "❌ The selected player isn't on that team anymore — "
                "pick again.", ephemeral=True
            )
            return
        members = await db.get_team_members(db_path, team["id"])
        member_ids = [m["discord_id"] for m in members]
        if card_owner_id not in member_ids:
            await interaction.followup.send(
                f"❌ The selected player isn't on team **{team['name']}**.",
                ephemeral=True,
            )
            return
        if t["format"] == "alt_shot" and len(members) != 2:
            await interaction.followup.send(
                f"❌ Alternate shot teams need exactly 2 players "
                f"({team['name']} has {len(members)}).",
                ephemeral=True,
            )
            return
        if t["format"] == "best_ball" and not 2 <= len(members) <= 4:
            await interaction.followup.send(
                f"❌ Best ball teams need 2-4 players "
                f"({team['name']} has {len(members)}).",
                ephemeral=True,
            )
            return
        if t["format"] == "scramble" and not 2 <= len(members) <= 4:
            await interaction.followup.send(
                f"❌ Scramble teams need 2-4 players "
                f"({team['name']} has {len(members)}).",
                ephemeral=True,
            )
            return
        team_id = team["id"]
        team_name = team["name"]
        if t["format"] in SHARED_CARD_FORMATS:
            card_player_id = None  # one shared team card

    # Verification: 2+ players in the tee time = partners present.
    player_count = await db.tee_time_player_count(db_path, tt["id"])
    status = "verified" if player_count >= 2 else "pending"
    # Crew-only edit rule: a submitted card can only be changed by crew
    # (admins, mods, tournament directors). First submissions stay open to
    # tee-time members.
    existing = await db.find_scorecard(
        db_path, t["id"],
        player_discord_id=card_player_id, team_id=team_id,
        tee_time_id=tt["id"], round_number=round_number,
    )
    if existing is not None and not await is_admin(interaction):
        await interaction.followup.send(
            "❌ That scorecard is already submitted — only crew (admins, mods,"
            " tournament directors) can change it. Ask a crew member to fix it.",
            ephemeral=True,
        )
        return
    card_id = await db.upsert_scorecard(
        db_path, t["id"], card_player_id, team_id, tt["id"], scores, status,
        submitted_by=submitter_id, round_number=round_number,
    )
    await leaderboard_render.refresh_leaderboard(bot, db_path, t["id"])

    owner_name = card_owner_id
    for p in await db.get_tee_time_players(db_path, tt["id"]):
        if p["discord_id"] == card_owner_id:
            owner_name = p["display_name"]
            break
    own_card = card_owner_id == submitter_id
    submitter_name = "you"
    if interaction.guild and not own_card:
        member = interaction.guild.get_member(int(submitter_id))
        if member:
            submitter_name = member.display_name
    who = "your card" if own_card else f"**{owner_name}**"
    entered_by = "Entered by you." if own_card else f"Entered by **{submitter_name}**."
    if team_id:
        who += f" (team **{team_name}**)"
    msg = (f"✅ Score submitted for {who}: **{sum(scores)}** "
           f"(card #{card_id}). {entered_by}")
    if len(await db.list_rounds(db_path, t["id"])) > 1:
        msg += f" — Round {round_number}."
    if status == "verified":
        msg += " Auto-verified — playing partners present. 🤝"
    else:
        msg += ("\n⏳ **Solo round** — this score is PENDING until "
                f"{admin_role_mention(interaction.guild)} verifies it "
                "with `/verify_score`.")
    await interaction.followup.send(msg, ephemeral=True)

    if status == "pending":
        try:
            await interaction.channel.send(
                f"⏳ <@{submitter_id}> submitted a **solo-round** score for "
                f"**{owner_name}** (card #{card_id}) in **{t['name']}** — "
                f"{admin_role_mention(interaction.guild)}, please review "
                "with `/verify_score`."
            )
        except discord.HTTPException:
            pass
def _card_embed(t: dict, card: dict, title_name: str, pars,
                show_round: bool = False) -> discord.Embed:
    scores = json.loads(card["holes_json"])
    n = len(scores)
    half = n // 2
    front = scores[:half]
    back = scores[half:]
    title = f"🃏 {title_name} — {t['name']}"
    if show_round:
        title += f" (Round {card.get('round_number', 1)})"
    embed = discord.Embed(title=title, color=0x1B6CA8)
    embed.description = " ".join(
        f"**{i + 1}**:{s}" for i, s in enumerate(scores)
    )
    embed.add_field(name="Front", value=str(sum(front)), inline=True)
    embed.add_field(name="Back", value=str(sum(back)), inline=True)
    embed.add_field(name="Total", value=f"**{card['total']}**", inline=True)
    if pars and len(pars) == n:
        tp = sl.format_to_par(sl.to_par(card["total"], pars))
        embed.add_field(name="To par", value=tp or "—", inline=True)
    status = "✅ Verified" if card["status"] == "verified" else "⏳ Pending verification"
    embed.add_field(name="Status", value=status, inline=True)
    embed.set_footer(text=f"Card #{card['id']}")
    return embed


class Scoring(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="submit_score", description="Submit your hole-by-hole score")
    @app_commands.autocomplete(tournament=inprogress_tournament_autocomplete,
                               tee_time=_tee_time_choices)
    @app_commands.describe(
        tournament="Defaults to the single in-progress tournament",
        tee_time="Defaults to your tee time (if you only have one)",
    )
    async def submit_score(
        self,
        interaction: discord.Interaction,
        tournament: Optional[int] = None,
        tee_time: Optional[int] = None,
    ):
        db_path = self.bot.db_path
        player_id = str(interaction.user.id)
        # Resolve tee time first (it implies the tournament).
        if tee_time:
            tt = await db.get_tee_time(db_path, tee_time)
            if not tt:
                await interaction.response.send_message(
                    "❌ That tee time wasn't found.", ephemeral=True
                )
                return
            t = await db.get_tournament(db_path, tt["tournament_id"])
            if not t or t["guild_id"] != str(interaction.guild_id):
                await interaction.response.send_message(
                    "❌ That tee time isn't in this server.", ephemeral=True
                )
                return
            if t["status"] != "in_progress":
                await interaction.response.send_message(
                    f"❌ **{t['name']}** hasn't started yet (or is complete).",
                    ephemeral=True,
                )
                return
            mine = await db.get_player_tee_time(db_path, t["id"], player_id)
            if not mine or mine["id"] != tt["id"]:
                await interaction.response.send_message(
                    f"❌ You're not in the **{tt['label']}** tee time. "
                    "Join it from `/tee_times` first.",
                    ephemeral=True,
                )
                return
        else:
            t, err = await resolve_tournament(interaction, tournament, ["in_progress"])
            if err:
                await interaction.response.send_message(err, ephemeral=True)
                return
            tt = await db.get_player_tee_time(db_path, t["id"], player_id)
            if not tt:
                await interaction.response.send_message(
                    f"❌ You're not in any tee time for **{t['name']}**. "
                    "Join or create one from `/tee_times` first.",
                    ephemeral=True,
                )
                return
        # Score entry unlocks only after the tee time has passed.
        if not sl.tee_time_passed(tt.get("starts_at") or ""):
            await interaction.response.send_message(
                await _locked_message(tt), ephemeral=True
            )
            return
        players = await db.get_tee_time_players(db_path, tt["id"])
        if not any(p["discord_id"] == player_id for p in players):
            await interaction.response.send_message(
                f"❌ You're not in the **{tt['label']}** tee time. "
                "Join it from `/tee_times` first.",
                ephemeral=True,
            )
            return
        teams: list[dict] = []
        if t["format"] in TEAM_FORMATS:
            teams = await db.get_player_teams(db_path, t["id"], player_id)
            if not teams:
                await interaction.response.send_message(
                    f"❌ You're not on a team in **{t['name']}**. "
                    "Create it first with `/team create`.",
                    ephemeral=True,
                )
                return
        rounds = await db.list_rounds(db_path, t["id"])
        view = ScoreEntryView(self.bot, t, tt, players, teams, player_id,
                              rounds=rounds)
        await interaction.response.send_message(
            embed=view.render(), view=view, ephemeral=True
        )
        view.message = await interaction.original_response()

    @app_commands.command(name="my_score", description="Show your submitted scorecard")
    @app_commands.autocomplete(tournament=active_tournament_autocomplete)
    @app_commands.describe(round_number="Which round to show (defaults to all rounds)")
    async def my_score(self, interaction: discord.Interaction,
                       tournament: Optional[int] = None,
                       round_number: Optional[Literal[1, 2, 3, 4, 5]] = None):
        t, err = await resolve_tournament(
            interaction, tournament, ["in_progress", "completed"]
        )
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        player_id = str(interaction.user.id)
        cards = await self._find_cards(t, player_id, round_number)
        if not cards:
            await interaction.response.send_message(
                f"You haven't submitted a score for **{t['name']}** yet.", ephemeral=True
            )
            return
        pars = [int(x) for x in t["pars"].split(",")] if t.get("pars") else None
        name = interaction.user.display_name
        multi = len(await db.list_rounds(self.bot.db_path, t["id"])) > 1
        await interaction.response.send_message(
            embeds=[_card_embed(t, c, name, pars, show_round=multi) for c in cards],
            ephemeral=True,
        )

    async def _find_cards(self, t: dict, target_id: str,
                          round_number: Optional[int]) -> list[dict]:
        """Latest card(s) for a player (or their team): one per round asked."""
        rounds = await db.list_rounds(self.bot.db_path, t["id"])
        want = [round_number] if round_number else [r["round_number"] for r in rounds]
        cards = []
        for rn in want:
            card = await db.get_latest_player_card(
                self.bot.db_path, t["id"], target_id, round_number=rn)
            if card is None and t["format"] in TEAM_FORMATS:
                for team in await db.get_player_teams(
                        self.bot.db_path, t["id"], target_id):
                    card = await db.get_latest_team_card(
                        self.bot.db_path, t["id"], team["id"], round_number=rn)
                    if card:
                        break
            if card:
                cards.append(card)
        return cards

    @app_commands.command(name="scorecard", description="Show someone's scorecard")
    @app_commands.autocomplete(tournament=active_tournament_autocomplete)
    @app_commands.describe(target="Whose scorecard to view",
                           round_number="Which round to show (defaults to all rounds)")
    async def scorecard(self, interaction: discord.Interaction,
                        target: discord.Member,
                        tournament: Optional[int] = None,
                        round_number: Optional[Literal[1, 2, 3, 4, 5]] = None):
        t, err = await resolve_tournament(
            interaction, tournament, ["in_progress", "completed"]
        )
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        tid = str(target.id)
        cards = await self._find_cards(t, tid, round_number)
        if not cards:
            await interaction.response.send_message(
                f"**{target.display_name}** hasn't submitted a score for "
                f"**{t['name']}** yet.",
                ephemeral=True,
            )
            return
        pars = [int(x) for x in t["pars"].split(",")] if t.get("pars") else None
        multi = len(await db.list_rounds(self.bot.db_path, t["id"])) > 1
        await interaction.response.send_message(
            embeds=[_card_embed(t, c, target.display_name, pars, show_round=multi)
                    for c in cards],
            ephemeral=True,
        )

    # ------------------------------ admin tools -----------------------------
    @app_commands.command(name="verify_score", description="Verify a pending scorecard (admin)")
    @app_commands.describe(card_id="The card # shown on the pending score")
    async def verify_score(self, interaction: discord.Interaction, card_id: int):
        if not await require_admin(interaction):
            return
        card = await db.get_scorecard(self.bot.db_path, card_id)
        if not card:
            await interaction.response.send_message(
                f"❌ No scorecard with ID {card_id}.", ephemeral=True
            )
            return
        if card["status"] == "verified":
            await interaction.response.send_message(
                f"Card #{card_id} is already verified.", ephemeral=True
            )
            return
        await db.verify_scorecard(self.bot.db_path, card_id, str(interaction.user.id))
        await leaderboard_render.refresh_leaderboard(
            self.bot, self.bot.db_path, card["tournament_id"]
        )
        t = await db.get_tournament(self.bot.db_path, card["tournament_id"])
        await interaction.response.send_message(
            f"✅ Card #{card_id} verified — it's now on the **{t['name']}** leaderboard.",
            ephemeral=True,
        )

    @app_commands.command(name="correct_score", description="Fix a hole score on someone's card (admin)")
    @app_commands.autocomplete(tournament=active_tournament_autocomplete)
    @app_commands.describe(target="Player whose card to fix", hole="Hole number (1-based)",
                           score="Corrected score for that hole",
                           round_number="Which round's card to fix (defaults to latest)")
    async def correct_score(self, interaction: discord.Interaction,
                            target: discord.Member, hole: int, score: int,
                            tournament: Optional[int] = None,
                            round_number: Optional[Literal[1, 2, 3, 4, 5]] = None):
        if not await require_admin(interaction):
            return
        if not 1 <= score <= 15:
            await interaction.response.send_message(
                "❌ Score must be 1-15.", ephemeral=True
            )
            return
        t, err = await resolve_tournament(
            interaction, tournament, ["in_progress", "completed"]
        )
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        if not 1 <= hole <= t["holes"]:
            await interaction.response.send_message(
                f"❌ Hole must be 1-{t['holes']}.", ephemeral=True
            )
            return
        tid = str(target.id)
        if round_number is None:
            # Legacy behavior: the most recently submitted card, any round.
            card = await db.get_latest_player_card(self.bot.db_path, t["id"], tid)
            if card is None and t["format"] in TEAM_FORMATS:
                for team in await db.get_player_teams(
                        self.bot.db_path, t["id"], tid):
                    card = await db.get_latest_team_card(
                        self.bot.db_path, t["id"], team["id"])
                    if card:
                        break
        else:
            cards = await self._find_cards(t, tid, round_number)
            card = cards[0] if cards else None
        if card is None:
            await interaction.response.send_message(
                f"❌ No scorecard found for **{target.display_name}** in **{t['name']}**.",
                ephemeral=True,
            )
            return
        updated = await db.correct_scorecard_hole(
            self.bot.db_path, card["id"], hole - 1, score
        )
        await leaderboard_render.refresh_leaderboard(self.bot, self.bot.db_path, t["id"])
        await interaction.response.send_message(
            f"✅ Card #{card['id']}: hole {hole} → **{score}**. "
            f"New total: **{updated['total']}**.",
            ephemeral=True,
        )

    @app_commands.command(name="dq", description="Disqualify a player: remove their scores (admin)")
    @app_commands.autocomplete(tournament=active_tournament_autocomplete)
    async def dq(self, interaction: discord.Interaction, target: discord.Member,
                 tournament: Optional[int] = None):
        if not await require_admin(interaction):
            return
        t, err = await resolve_tournament(
            interaction, tournament, ["registration_open", "in_progress"]
        )
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        tid = str(target.id)
        removed = await db.delete_player_cards(self.bot.db_path, t["id"], tid)
        team_note = ""
        if t["format"] in SHARED_CARD_FORMATS:
            # The shared team card can't survive a DQ'd member.
            for team in await db.get_player_teams(self.bot.db_path, t["id"], tid):
                card = await db.get_latest_team_card(self.bot.db_path, t["id"], team["id"])
                if card:
                    await db.delete_scorecard(self.bot.db_path, card["id"])
                    team_note = f" Team **{team['name']}**'s card was also removed."
        await db.unregister_player(self.bot.db_path, t["id"], tid)
        tt = await db.get_player_tee_time(self.bot.db_path, t["id"], tid)
        if tt:
            await db.leave_tee_time(self.bot.db_path, tt["id"], tid)
        await leaderboard_render.refresh_leaderboard(self.bot, self.bot.db_path, t["id"])
        await interaction.channel.send(
            f"🚫 **{target.display_name}** has been disqualified from **{t['name']}** "
            f"({removed} scorecard(s) removed).{team_note}"
        )
        await interaction.response.send_message("✅ DQ processed.", ephemeral=True)

    # -------------------------------- teams ---------------------------------
    team = app_commands.Group(name="team", description="Manage teams (best ball / alternate shot / scramble)")

    @team.command(name="create", description="Create a team")
    @app_commands.autocomplete(tournament=active_tournament_autocomplete)
    async def team_create(self, interaction: discord.Interaction, name: str,
                          tournament: Optional[int] = None):
        t, err = await resolve_tournament(
            interaction, tournament, ["registration_open", "in_progress"]
        )
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        if t["format"] not in TEAM_FORMATS:
            await interaction.response.send_message(
                f"❌ **{t['name']}** is {t['format']} — teams only apply to "
                "best ball / alternate shot / scramble.",
                ephemeral=True,
            )
            return
        name = name.strip()[:50]
        if not name:
            await interaction.response.send_message("❌ Give the team a name.",
                                                    ephemeral=True)
            return
        if await db.get_team_by_name(self.bot.db_path, t["id"], name):
            await interaction.response.send_message(
                f"❌ A team named **{name}** already exists.", ephemeral=True
            )
            return
        team_id = await db.create_team(self.bot.db_path, t["id"], name,
                                       str(interaction.user.id))
        await db.add_team_member(self.bot.db_path, team_id, str(interaction.user.id))
        need = "exactly 2" if t["format"] == "alt_shot" else "2-4"
        await interaction.response.send_message(
            f"✅ Team **{name}** created (you're in). Add teammates with "
            f"`/team add` — {t['format']} needs {need} players.",
            ephemeral=True,
        )

    @team.command(name="add", description="Add a player to your team")
    @app_commands.autocomplete(tournament=active_tournament_autocomplete)
    async def team_add(self, interaction: discord.Interaction, member: discord.Member,
                       tournament: Optional[int] = None):
        t, err = await resolve_tournament(
            interaction, tournament, ["registration_open", "in_progress"]
        )
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        teams = await db.get_player_teams(self.bot.db_path, t["id"],
                                          str(interaction.user.id))
        if not teams:
            await interaction.response.send_message(
                "❌ You're not on a team — create one with `/team create` first.",
                ephemeral=True,
            )
            return
        team = teams[0]
        is_creator = team["created_by"] == str(interaction.user.id)
        if not is_creator and not await require_admin(interaction):
            return
        if not await db.is_registered(self.bot.db_path, t["id"], str(member.id)):
            await interaction.response.send_message(
                f"❌ {member.display_name} isn't registered for **{t['name']}**.",
                ephemeral=True,
            )
            return
        members = await db.get_team_members(self.bot.db_path, team["id"])
        cap = 2 if t["format"] == "alt_shot" else 4
        if len(members) >= cap:
            await interaction.response.send_message(
                f"❌ Team **{team['name']}** is full ({cap}).", ephemeral=True
            )
            return
        new = await db.add_team_member(self.bot.db_path, team["id"], str(member.id))
        await interaction.response.send_message(
            f"✅ {member.display_name} {'joined' if new else 'is already on'} "
            f"team **{team['name']}** ({len(members) + (1 if new else 0)}/{cap}).",
            ephemeral=True,
        )

    @team.command(name="remove", description="Remove a player from your team")
    @app_commands.autocomplete(tournament=active_tournament_autocomplete)
    async def team_remove(self, interaction: discord.Interaction, member: discord.Member,
                          tournament: Optional[int] = None):
        t, err = await resolve_tournament(
            interaction, tournament, ["registration_open", "in_progress"]
        )
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        teams = await db.get_player_teams(self.bot.db_path, t["id"],
                                          str(interaction.user.id))
        if not teams:
            await interaction.response.send_message("❌ You're not on a team.",
                                                    ephemeral=True)
            return
        team = teams[0]
        if team["created_by"] != str(interaction.user.id) and not await require_admin(interaction):
            return
        removed = await db.remove_team_member(self.bot.db_path, team["id"],
                                              str(member.id))
        await interaction.response.send_message(
            f"✅ {member.display_name} {'removed from' if removed else 'was not on'} "
            f"team **{team['name']}**.",
            ephemeral=True,
        )

    @team.command(name="leave", description="Leave your team")
    @app_commands.autocomplete(tournament=active_tournament_autocomplete)
    async def team_leave(self, interaction: discord.Interaction,
                         tournament: Optional[int] = None):
        t, err = await resolve_tournament(
            interaction, tournament, ["registration_open", "in_progress"]
        )
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        teams = await db.get_player_teams(self.bot.db_path, t["id"],
                                          str(interaction.user.id))
        if not teams:
            await interaction.response.send_message("❌ You're not on a team.",
                                                    ephemeral=True)
            return
        await db.remove_team_member(self.bot.db_path, teams[0]["id"],
                                    str(interaction.user.id))
        await interaction.response.send_message(
            f"✅ You left team **{teams[0]['name']}**.", ephemeral=True
        )

    @team.command(name="list", description="List teams and members")
    @app_commands.autocomplete(tournament=active_tournament_autocomplete)
    async def team_list(self, interaction: discord.Interaction,
                        tournament: Optional[int] = None):
        t, err = await resolve_tournament(
            interaction, tournament,
            ["registration_open", "in_progress", "completed"],
        )
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        teams = await db.get_teams(self.bot.db_path, t["id"])
        embed = discord.Embed(title=f"👥 {t['name']} — Teams ({len(teams)})",
                              color=0x1B6CA8)
        if not teams:
            embed.description = "No teams yet."
        for team in teams[:25]:
            members = await db.get_team_members(self.bot.db_path, team["id"])
            names = ", ".join(m["display_name"] for m in members) or "—"
            embed.add_field(name=f"{team['name']} ({len(members)})",
                            value=names, inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Scoring(bot))

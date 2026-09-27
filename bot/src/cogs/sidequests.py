"""Side quests: casual round logging for the Range (non-tournament play).

`/sidequest log` records a round with your buddies — any of the three
recorded formats (stroke, alt_shot, scramble) — and `/sidequest records`
shows history plus personal/course bests per format.
"""
import re
import uuid
from typing import Literal, Optional

import discord
from discord import app_commands
from discord.ext import commands

from src import db
from src import golfplus_courses
from src import scoring_logic as sl

SIDEQUEST_FORMATS = ("stroke", "alt_shot", "scramble")
FORMAT_LABELS = {
    "stroke": "Stroke play",
    "alt_shot": "Alternate shot",
    "scramble": "Scramble",
}
_MENTION_RE = re.compile(r"<@!?(\d+)>")

# (min_players, max_players, team_card) per format. team_card=True means the
# group submits a single shared scorecard instead of one per player.
_FORMAT_RULES = {
    "stroke": (1, 8, False),
    "alt_shot": (2, 2, True),
    "scramble": (2, 4, True),
}


async def course_autocomplete(interaction: discord.Interaction, current: str):
    """Suggest Golf+ courses; free text is still accepted (not validated)."""
    return [
        app_commands.Choice(name=name, value=value)
        for name, value in golfplus_courses.course_choices(current)
    ]


def _resolve_player_name(guild: discord.Guild | None, token: str) -> tuple[str, str | None]:
    """Turn a players-list token into a display name.

    Resolves Discord mentions to the member's display name when possible;
    otherwise keeps the raw token. Returns (display_name, discord_id|None).
    """
    m = _MENTION_RE.fullmatch(token.strip())
    if m and guild is not None:
        member = guild.get_member(int(m.group(1)))
        if member is not None:
            return member.display_name[:80], str(member.id)
    return token.strip()[:80], None


class SideQuests(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    sidequest = app_commands.Group(
        name="sidequest", description="Log casual rounds & view Range records"
    )

    @sidequest.command(name="log", description="Log a casual round with your buddies")
    @app_commands.autocomplete(course=course_autocomplete)
    @app_commands.describe(
        format="Scoring format",
        course="Course name in Golf+",
        holes="9 or 18 holes",
        players="Buddies, e.g. 'Alice, Bob' (mentions work too)",
        scores="Hole scores, comma-separated. Stroke: one card per player "
               "in order, separated by ';'. Team formats: one team card.",
    )
    async def sidequest_log(
        self,
        interaction: discord.Interaction,
        format: Literal["stroke", "alt_shot", "scramble"],
        course: str,
        holes: Literal[9, 18],
        players: str,
        scores: str,
    ):
        db_path = self.bot.db_path
        guild_id = str(interaction.guild_id)
        try:
            names = sl.parse_names_list(players)
        except ValueError as e:
            await interaction.response.send_message(f"❌ {e}", ephemeral=True)
            return
        min_p, max_p, team_card = _FORMAT_RULES[format]
        need = "exactly 2" if min_p == max_p == 2 else f"{min_p}-{max_p}"
        if not min_p <= len(names) <= max_p:
            await interaction.response.send_message(
                f"❌ {FORMAT_LABELS[format]} needs {need} players "
                f"(you listed {len(names)}).",
                ephemeral=True,
            )
            return
        try:
            cards = sl.split_score_cards(scores)
        except ValueError as e:
            await interaction.response.send_message(f"❌ {e}", ephemeral=True)
            return
        if team_card and len(cards) != 1:
            await interaction.response.send_message(
                f"❌ {FORMAT_LABELS[format]} takes a single team scorecard — "
                f"you gave {len(cards)} (separate cards with ';').",
                ephemeral=True,
            )
            return
        if not team_card and len(cards) != len(names):
            await interaction.response.send_message(
                f"❌ Stroke play needs one scorecard per player, in order — "
                f"{len(names)} players but {len(cards)} cards.",
                ephemeral=True,
            )
            return
        parsed_cards: list[list[int]] = []
        for c in cards:
            try:
                parsed_cards.append(sl.parse_hole_scores(c, holes))
            except ValueError as e:
                await interaction.response.send_message(f"❌ {e}", ephemeral=True)
                return

        # Resolve mentions → display names; remember real members for records.
        resolved: list[tuple[str, str | None]] = []
        for token in names:
            name, did = _resolve_player_name(interaction.guild, token)
            resolved.append((name, did))
            if did:
                member = interaction.guild.get_member(int(did))
                if member is not None:
                    await db.upsert_player(db_path, did, member.display_name[:80])

        if team_card:
            entries = [(name, parsed_cards[0]) for name, _ in resolved]
        else:
            entries = [(name, card) for (name, _), card in zip(resolved, parsed_cards)]

        group_id = uuid.uuid4().hex
        await db.log_side_quest(
            db_path, guild_id, group_id, format, course.strip()[:80], holes,
            entries, str(interaction.user.id),
        )

        label = FORMAT_LABELS[format]
        embed = discord.Embed(
            title=f"🎯 Side quest logged — {label}",
            description=f"**{course.strip()[:80]}** • {holes} holes",
            color=0x2E7D32,
        )
        if team_card:
            team_total = sum(parsed_cards[0])
            roster = ", ".join(name for name, _ in resolved)
            embed.add_field(name="Team", value=roster, inline=False)
            embed.add_field(name="Team total", value=f"**{team_total}**", inline=False)
        else:
            lines = [
                f"• {name} — **{sum(card)}**"
                for (name, _), card in zip(resolved, parsed_cards)
            ]
            embed.add_field(name="Scores", value="\n".join(lines), inline=False)
        embed.set_footer(text=f"Logged by {interaction.user.display_name}")
        await interaction.response.send_message(embed=embed)

    @sidequest.command(name="records", description="Show Range history & bests")
    @app_commands.describe(
        player="Filter to one player",
        course="Filter to one course",
        format="Filter to one format",
    )
    async def sidequest_records(
        self,
        interaction: discord.Interaction,
        player: Optional[discord.Member] = None,
        course: Optional[str] = None,
        format: Optional[Literal["stroke", "alt_shot", "scramble"]] = None,
    ):
        db_path = self.bot.db_path
        guild_id = str(interaction.guild_id)
        player_name = player.display_name if player else None
        bests = await db.side_quest_bests(
            db_path, guild_id, player_name=player_name,
            course=course.strip() if course else None, format=format,
        )
        recent = await db.side_quest_recent(db_path, guild_id, limit=10)

        bits = []
        if player:
            bits.append(player.display_name)
        if course:
            bits.append(course.strip())
        if format:
            bits.append(FORMAT_LABELS[format])
        title = "🎯 Side Quest Records" + (f" — {', '.join(bits)}" if bits else "")

        embed = discord.Embed(title=title, color=0x1B6CA8)
        if bests:
            lines = []
            for b in bests[:20]:
                label = FORMAT_LABELS.get(b["format"], b["format"])
                rnd = f" ({b['rounds']} round{'s' if b['rounds'] != 1 else ''})"
                lines.append(
                    f"• {label} @ **{b['course']}** — {b['player_name']}: "
                    f"**{b['best']}**{rnd}"
                )
            embed.add_field(name="🏅 Bests (lowest total per player)",
                            value="\n".join(lines), inline=False)
        else:
            embed.add_field(name="🏅 Bests",
                            value="No rounds match — log one with `/sidequest log`.",
                            inline=False)
        if recent and not player_name and not course and not format:
            lines = []
            for r in recent:
                label = FORMAT_LABELS.get(r["format"], r["format"])
                who = ", ".join(
                    f"{p['player_name']} ({p['total']})" for p in r["players"][:6]
                )
                day = (r["logged_at"] or "")[:10]
                lines.append(
                    f"• {day} — {label} @ **{r['course']}** ({r['holes']}): {who}"
                )
            embed.add_field(name="🕘 Recent rounds", value="\n".join(lines),
                            inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(SideQuests(bot))

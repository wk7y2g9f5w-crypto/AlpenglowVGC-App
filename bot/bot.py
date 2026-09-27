"""Entry point: python bot.py

Guild-scoped slash command sync when GUILD_ID is set (instant registration
while testing), otherwise global sync.
No privileged intents required.
"""
import asyncio
import os
import sys

import discord
from discord.ext import commands

from src import config, db

COGS = [
    "src.cogs.tournaments",
    "src.cogs.registration",
    "src.cogs.teetimes",
    "src.cogs.scoring",
    "src.cogs.leaderboard",
    "src.cogs.matches",
    "src.cogs.sidequests",
    "src.cogs.teesheet",
    "src.cogs.profile",
    "src.cogs.onboarding",
    "src.cogs.seasons",
    "src.cogs.stats",
    "src.cogs.guides",
]


class TournamentBot(commands.Bot):
    def __init__(self):
        # Route through the egress proxy when one is configured (sandboxed envs).
        # aiohttp reads proxy credentials straight from the URL userinfo.
        proxy_url = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
        super().__init__(
            command_prefix="!",
            intents=discord.Intents.default(),  # no privileged intents needed
            help_command=None,
            proxy=proxy_url,
        )
        self.db_path = config.DB_PATH

    async def setup_hook(self):
        await db.init_db(self.db_path)
        for ext in COGS:
            await self.load_extension(ext)
        if config.GUILD_ID:
            guild = discord.Object(id=config.GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            print(f"Synced {len(synced)} commands to guild {config.GUILD_ID}")
        else:
            synced = await self.tree.sync()
            print(f"Synced {len(synced)} commands globally")

    async def on_ready(self):
        print(f"Logged in as {self.user} (id={self.user.id})")
        print(f"Connected to {len(self.guilds)} guild(s)")
        from src import teesheet as ts
        from src.cogs import guides as guides_mod

        started = getattr(self, "_teesheet_started", None)
        if started is None:
            started = self._teesheet_started = set()
        for guild in self.guilds:
            if guild.id in started:
                continue
            try:
                await ts.startup(self, str(guild.id))
                started.add(guild.id)
            except Exception as e:  # noqa: BLE001 - board is best-effort
                print(f"tee-sheet startup failed for guild {guild.id}: {e}")
            try:
                await guides_mod.startup(self, str(guild.id))
            except Exception as e:  # noqa: BLE001 - guides are best-effort
                print(f"guide startup failed for guild {guild.id}: {e}")


async def main():
    if not config.DISCORD_TOKEN:
        print("ERROR: DISCORD_TOKEN is not set. Copy .env.example to .env and fill it in.")
        sys.exit(1)
    bot = TournamentBot()
    async with bot:
        await bot.start(config.DISCORD_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())

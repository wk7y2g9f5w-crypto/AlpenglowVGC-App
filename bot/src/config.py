"""Bot configuration loaded from environment (.env supported)."""
import os

from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN: str = os.getenv("DISCORD_TOKEN", "")

# Guild ID for guild-scoped slash command registration (instant updates while
# testing). If 0/empty, commands sync globally (can take up to an hour).
_guild_raw = os.getenv("GUILD_ID", "").strip()
GUILD_ID: int = int(_guild_raw) if _guild_raw.isdigit() else 0

DB_PATH: str = os.getenv("DB_PATH", "tournament_bot.db")

# Optional: public download link for the companion app (e.g. TestFlight).
# When set, tournament announcements in #event-signups show a "Get the app"
# link button instead of the Discord-native Register button.
APP_DOWNLOAD_URL: str = os.getenv("APP_DOWNLOAD_URL", "").strip()

# Role that gates admin commands (auto-created if missing and the bot has
# Manage Roles). Members with Manage Server permission are always admins.
ADMIN_ROLE_NAME: str = "Tournament Admin"

# Times entered for tee times are interpreted as UTC (see README).

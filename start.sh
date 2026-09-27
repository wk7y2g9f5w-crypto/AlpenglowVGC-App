#!/bin/bash
# Render start script: Discord bot + FastAPI share one SQLite DB on the disk.
set -e

# Seed the database from the snapshot on first boot only.
# The Render disk persists, so this never overwrites live data.
if [ ! -f /data/tournament_bot.db ]; then
  echo "Seeding database from snapshot..."
  cp bot/seed/tournament_bot.db /data/tournament_bot.db
fi

# Discord bot in the background (auto-restarts if it exits).
while true; do
  (cd bot && python bot.py)
  echo "Bot exited, restarting in 5s..."
  sleep 5
done &

# FastAPI in the foreground on Render's port.
exec uvicorn api.main:app --host 0.0.0.0 --port "${PORT:-10000}"

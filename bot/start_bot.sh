#!/bin/bash
# Start the Fairway tournament bot detached so it survives the launching shell.
# Tracks the PID in bot.pid; safe to run repeatedly.
cd ~/workspace/golf-tournament-bot || exit 1
if [ -f bot.pid ] && kill -0 "$(cat bot.pid)" 2>/dev/null; then
  echo "bot already running pid $(cat bot.pid)"
  exit 0
fi
rm -f bot.pid
setsid nohup .venv/bin/python -u bot.py >> bot.log 2>&1 < /dev/null &
echo $! > bot.pid
echo "bot started pid $(cat bot.pid)"

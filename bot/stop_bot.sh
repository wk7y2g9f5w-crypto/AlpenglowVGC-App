#!/bin/bash
# Stop the Fairway tournament bot.
PID_FILE=~/workspace/golf-tournament-bot/bot.pid
if [ -f "$PID_FILE" ]; then
  kill "$(cat "$PID_FILE")" 2>/dev/null && echo "stopped pid $(cat "$PID_FILE")"
  rm -f "$PID_FILE"
else
  echo "not running (no pid file)"
fi

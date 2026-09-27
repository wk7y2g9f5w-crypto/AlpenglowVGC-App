# ⛳ Golf+ Tournament Bot

A Discord bot for running Golf+ (VR golf) community tournaments: per-event
registration, tee-time booking with reminders, manual score submission with
playing-partner verification, match-play reporting, team formats (best ball /
alternate shot), and a **live leaderboard** that updates itself after every
score.

Golf+ has no public score API, so all scores are player-submitted through
Discord. Trust comes from **playing-partner presence**: a scorecard
auto-verifies when 2+ players share the tee time; solo rounds stay pending
until an admin verifies them.

## Features

- **Tournaments** — `/tournament create|list|start|complete` (admin). Formats:
  `stroke`, `match`, `best_ball`, `alt_shot`, `scramble`; 9 or 18 holes.
  Tournaments carry a start date and date range, and `/tournament list` shows
  them side by side so you can register for none, some, or all running events.
  The `course` field has a Golf+ course picker (autocomplete); free text is
  still accepted. Each tournament records Golf+ round settings: tee position
  (Front/Middle/Back), pin position (Black/White/Red), wind (Low/Moderate/
  Severe), and green speed (Very Fast/Pro).
- **Registration** — `/register` (or the ✅ button on the announcement).
  Eligibility = registered for that event. Players get a `⛳ <tournament>`
  role.
- **Tee times** — `/tee_time create` (times in your local timezone, set with
  `/set_timezone`); joining someone else's
  tee time sends them a request they approve with ✅/❌ buttons
  (or use `/tee_time request`). One tee time per player per tournament.
  Automatic 30-min and 5-min reminders tag the players.
- **Side quests** — `/sidequest log` records a casual round with your buddies
  (`stroke`, `alt_shot`, or `scramble`); `/sidequest records` shows history
  and personal/course bests per format. Course picker included.
- **Command guides** — the bot auto-posts a 📖 Player Command Guide in the
  read-only `#command-guide` channel (no admin commands) and a 🛠️ Crew
  Command Guide in the private `#crew-guide` channel. `/guide refresh`
  (admin) re-posts them.
- **Scoring** — `/submit_score` opens a tap-to-enter scorecard (score buttons
  per hole, custom entry for blowups) — enter your own card or a playing
  partner's in your tee time. Unlocks after your tee time passes.
  Resubmitting updates the card. `/my_score`, `/scorecard @player`.
- **Verification** — ≥2 players in a tee time → auto-verified 🤝. Solo round →
  ⏳ pending until an admin runs `/verify_score <card_id>`.
- **Match play** — `/report_match @opponent win|loss|tie`, opponent confirms
  with a button; `/confirm_match` admin override.
- **Teams** — `/team create|add|remove|leave|list` for best ball (2–4),
  alternate shot (exactly 2), and scramble (2–4, one shared team card).
- **Live leaderboard** — `/leaderboard`, plus a pinned board message posted at
  `/tournament start` that re-renders after every submit/verify/correction/DQ.
  Pending scores are shown but excluded from ranking.
- **Lead-change notifications** — when a new score takes the tournament lead,
  the bot posts a 🔄 alert in the leaderboard channel automatically.
- **Season standings** — `/season create|add_tournament|standings|complete|list`
  (admin). Link tournaments to a season and they award points at completion
  (100 for 1st → 5 for 11th+; ties share points); `/season standings` shows
  the crew's points race.
- **Player stats** — `/stats [player]` aggregates verified tournament rounds:
  best/average round, birdies, pars, bogeys, blowups, best par streak, and
  match-play W-L-T.
- **Admin tools** — `/correct_score`, `/dq`, role-gated by the `Tournament
  Admin` role (auto-created) or Manage Server permission.

## Setup from zero

### 1. Create the bot application

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications)
   → **New Application**, name it (e.g. "Golf Tournament Bot").
2. Open **Bot** → **Reset Token** → copy the token. **Keep it secret.**
3. **Privileged intents: none required.** Leave *Presence*, *Server Members*,
   and *Message Content* intents **off** — this bot only uses slash commands,
   buttons, and modals, which work with default intents.

### 2. Invite the bot to your test server

Replace `YOUR_CLIENT_ID` with the **Application ID** (Portal → General
Information → Application ID) and open this URL:

```
https://discord.com/oauth2/authorize?client_id=YOUR_CLIENT_ID&permissions=2416134144&scope=bot%20applications.commands
```

The permission integer `2416134144` grants exactly:

| Permission | Bit |
|---|---|
| Send Messages | `1 << 11` |
| Embed Links | `1 << 14` |
| Read Message History | `1 << 16` |
| Pin Messages | `1 << 17` |
| Manage Roles | `1 << 28` |
| Use Application Commands | `1 << 31` |

(`scope=bot applications.commands` is what makes slash commands work.)

### 3. Role hierarchy (important)

The bot assigns two kinds of roles: `Tournament Admin` and per-tournament
`⛳ <name>` participant roles. In **Server Settings → Roles**, drag the bot's
role **above** any role it needs to assign. If it sits below, role assignment
fails and the bot will tell you.

### 4. Run it locally

```bash
cd golf-tournament-bot
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env: DISCORD_TOKEN=..., GUILD_ID=<right-click server icon → Copy Server ID>
python bot.py
```

`GUILD_ID` scopes slash-command registration to your test server so commands
appear **instantly**. Without it, commands sync globally (up to ~1 hour).

You should see `Logged in as ...` and `Synced N commands to guild ...`.
Try `/tournament create` in your test server.

### 5. Host it 24/7 (Railway)

The reminder loop and persistent buttons need the bot always online:

1. Push this folder to a GitHub repo.
2. [Railway](https://railway.app) → **New Project** → **Deploy from GitHub**.
3. Add variables: `DISCORD_TOKEN`, `GUILD_ID` (your main server, or leave
   empty for global sync), `DB_PATH` (default `tournament_bot.db`).
4. Railway runs `python bot.py` — set the start command if it doesn't detect it.
5. ⚠️ SQLite lives on the container's disk: enable a **persistent volume**
   mounted where `DB_PATH` points, or the database resets on every redeploy.
   (Alternative: swap `src/db.py` for Postgres later — the function signatures
   are the seam.)

## How the verification rule works

When a score is submitted, the bot counts players in that tee time:

- **2+ players** → card is `verified` immediately ("auto-verified: playing
  partners present") and hits the leaderboard.
- **1 player (solo round)** → card stays `pending`: it appears on the board
  marked ⏳ but is **excluded from ranking**, and a notice is posted asking an
  admin to review with `/verify_score <card_id>`.

This is the honor system with a paper trail: partners present beats partners
absent, and every card shows its verification status.

## Command reference

| Command | Who | What |
|---|---|---|
| `/tournament create` | Admin | New tournament (format, holes, course, start/end dates, optional pars) + announcement with Register button |
| `/tournament list` | Anyone | Tournaments with format, dates, and statuses — pick which to play |
| `/tournament start` | Admin | Opens play, posts + pins the live leaderboard |
| `/tournament complete` | Admin | Closes it, posts final standings (pending join requests expire) |
| `/register`, `/unregister`, `/roster` | Anyone | Per-event eligibility and roster |
| `/tee_time create` | Registered | Create a tee time (`YYYY-MM-DD`, `HH:MM` your local time); you auto-join |
| `/tee_times` | Anyone | Tee sheet with Request / Leave buttons (requests need creator approval) |
| `/tee_time request` | Registered | Request to join someone's tee time |
| `/tee_time leave` | Anyone | Leave your tee time |
| `/sidequest log` | Anyone | Log a casual round with buddies (stroke / alt_shot / scramble) |
| `/sidequest records` | Anyone | Range history + bests per player/format/course |
| `/submit_score` | Registered | Modal: comma-separated hole scores (team name too, for team formats) |
| `/my_score`, `/scorecard @user` | Anyone | Hole-by-hole cards |
| `/leaderboard` | Anyone | Live board for any tournament |
| `/season create` | Admin | Start a season points race |
| `/season add_tournament` | Admin | Link a tournament to the active season |
| `/season standings` | Anyone | Season points standings |
| `/season complete` | Admin | Close the season, post final standings |
| `/season list` | Anyone | List seasons |
| `/stats [player]` | Anyone | Career stats from verified rounds |
| `/report_match @opp win\|loss\|tie` | Registered | Report match result; opponent confirms via button |
| `/matches`, `/confirm_match` | Anyone / Admin | Results list; admin override |
| `/team create\|add\|remove\|leave\|list` | Registered | Team management (best ball 2–4, alt shot exactly 2) |
| `/verify_score <card_id>` | Admin | Verify a pending solo-round card |
| `/guide refresh` | Admin | Re-post the command guides in #command-guide and #crew-guide |
| `/correct_score @user <hole> <score>` | Admin | Fix a hole on someone's card |
| `/dq @user` | Admin | Remove a player's scores + registration |

## Notes & limits

- **Times are local.** Enter tee times in your own timezone (set once with
  `/set_timezone`, default UTC); they're stored as UTC and Discord renders
  them in each viewer's local timezone via `<t:…>` timestamps.
- **One card per player per tee time** (latest submission wins); team formats
  aggregate each member's latest verified card.
- Buttons on old messages survive restarts for Register and match-confirm;
  `/tee_times` buttons last 30 minutes — just re-run the command.
- Tests: `python -m unittest discover -s tests` (pure scoring logic, no
  Discord connection needed).

## Project layout

```
bot.py                  entry point
requirements.txt
.env.example
src/
  config.py             env config
  db.py                 async SQLite layer (all queries parameterized)
  scoring_logic.py      pure scoring math (unit-tested, no discord import)
  leaderboard_render.py single leaderboard renderer (command + auto-refresh)
  cogs/
    common.py           admin gate, tournament autocomplete/resolution
    tournaments.py      /tournament group + Register button
    registration.py     /register /unregister /roster
    teetimes.py         /tee_time group, /tee_times, reminder loop,
                      join-request approvals
    scoring.py          /submit_score modal, cards, /team group, admin tools
    leaderboard.py      /leaderboard
    matches.py          /report_match, confirmations
    sidequests.py       /sidequest log|records (casual rounds & bests)
tests/
  test_scoring_logic.py 27 unit tests
```

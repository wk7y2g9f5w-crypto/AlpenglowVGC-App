# Alpenglow VGC companion API

FastAPI JSON service exposing the Discord golf tournament bot's data to the
Alpenglow VGC mobile app.

## How it works

- Shares the bot's SQLite database read/write (aiosqlite, short transactions).
- **Reuses the bot's own modules** (`src.db`, `src.scoring_logic`,
  `src.leaderboard_render`, plus pure helpers `parse_in_tz` / `_parse_pars`
  from `src.cogs`) so the app and Discord always compute rankings the same way.
- Never touches the bot itself: no Discord roles/channels/messages, no bot
  restart, additive-only use of the shared DB file.
- Auth: `Authorization: Bearer <discord_user_oauth_token>` on every request
  except `GET /api/health`. The token is validated per request against
  `https://discord.com/api/v10/users/@me` and is never stored or logged.
  Invalid/expired → `401 {"detail": "Invalid Discord token"}`.

## Run

```bash
cd ~/workspace/golf-companion-app/api
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python main.py          # port 8420; set PORT to override
# or:
.venv/bin/uvicorn main:app --port 8420
```

Env knobs:

- `PORT` — listen port (default 8420)
- `BOT_DIR` — path to the bot checkout (default
  `~/workspace/golf-tournament-bot`). The API loads `BOT_DIR/.env` for
  `GUILD_ID` and `DB_PATH` (relative `DB_PATH` resolves against `BOT_DIR`).

Startup verifies the shared DB file exists and has a `players` table.

## Endpoints

All under `/api`; `starts_at` is always ISO-8601 UTC in responses.

| Method | Path | Notes |
|---|---|---|
| GET | /api/health | no auth → `{"ok": true}` |
| GET | /api/tournaments | guild tournaments + `registered` flag for caller |
| POST | /api/tournaments/{id}/register | 404 unknown guild tournament; 409 `registration_closed`; 409 `timezone_required` |
| DELETE | /api/tournaments/{id}/register | withdraw (also leaves tee time) |
| GET | /api/tournaments/{id}/tee-times | with player rosters |
| POST | /api/tournaments/{id}/tee-times | `{label, date, time, max_players}`; date/time interpreted in the creator's saved timezone; creator auto-joins |
| POST | /api/tee-times/{id}/join | 409 `tee_time_full`, 404 missing |
| POST | /api/tee-times/{id}/leave | `{"left": true}` |
| POST | /api/tee-times/{id}/request | join request; 409 `request_pending` |
| GET | /api/tee-times/{id}/requests | creator only (403 otherwise) |
| POST | /api/tee-times/{id}/requests/{req_id}/approve | creator only; re-validates + adds player like the bot |
| POST | /api/tee-times/{id}/requests/{req_id}/decline | creator only |
| GET | /api/tee-times/{id}/scorecard | caller's latest card for this tee time (`{"card": null}` when none) |
| PUT | /api/tee-times/{id}/scorecard | `{player_discord_id, scores[]}`; 409 `tee_time_not_passed` until the tee time has started |
| GET | /api/tournaments/{id}/leaderboard | stroke / team / match, same ranking helpers as Discord |
| GET | /api/seasons/standings | 404 `no_active_season` when none |
| GET | /api/players/me | profile row |
| PATCH | /api/players/me | `{timezone?, golfplus_handle?}`; 422 on invalid IANA name; empty handle → unlink |
| GET | /api/players/me/stats | verified rounds + match W-L-T, mirrors `/stats` |

## Tests

```bash
cd ~/workspace/golf-companion-app/api
.venv/bin/python -m unittest discover -s tests -v
```

Tests use a fresh temp DB (`db.init_db`, never the live file) and monkeypatch
the Discord token validator — no network, no bot interaction.

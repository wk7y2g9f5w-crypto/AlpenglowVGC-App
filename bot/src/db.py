"""Async SQLite storage layer (aiosqlite).

All queries are parameterized (``?`` placeholders) — no f-string SQL.
Each function opens its own short-lived connection, which is fine at the
scale of a Discord community bot.
"""
import json
from datetime import datetime, timedelta, timezone

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS tournaments(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  guild_id TEXT NOT NULL,
  name TEXT NOT NULL,
  format TEXT NOT NULL CHECK(format IN ('stroke','match','best_ball','alt_shot','scramble')),
  holes INTEGER NOT NULL CHECK(holes IN (9,18)),
  course TEXT NOT NULL,
  pars TEXT,
  description TEXT,
  status TEXT NOT NULL DEFAULT 'registration_open'
    CHECK(status IN ('registration_open','in_progress','completed')),
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  leaderboard_channel_id TEXT,
  leaderboard_message_id TEXT,
  start_date TEXT,
  end_date TEXT,
  tee_position TEXT NOT NULL DEFAULT 'middle'
    CHECK(tee_position IN ('front','middle','back')),
  pin_position TEXT NOT NULL DEFAULT 'white'
    CHECK(pin_position IN ('black','white','red')),
  wind_strength TEXT NOT NULL DEFAULT 'moderate'
    CHECK(wind_strength IN ('low','moderate','severe')),
  green_speed TEXT NOT NULL DEFAULT 'pro'
    CHECK(green_speed IN ('veryfast','pro'))
);
CREATE TABLE IF NOT EXISTS rounds(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tournament_id INTEGER NOT NULL REFERENCES tournaments(id) ON DELETE CASCADE,
  round_number INTEGER NOT NULL CHECK(round_number BETWEEN 1 AND 5),
  tee_position TEXT NOT NULL DEFAULT 'middle'
    CHECK(tee_position IN ('front','middle','back')),
  pin_position TEXT NOT NULL DEFAULT 'white'
    CHECK(pin_position IN ('black','white','red')),
  wind_strength TEXT NOT NULL DEFAULT 'moderate'
    CHECK(wind_strength IN ('low','moderate','severe')),
  start_date TEXT,
  end_date TEXT,
  UNIQUE(tournament_id, round_number)
);
CREATE TABLE IF NOT EXISTS players(
  discord_id TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  golfplus_handle TEXT,
  timezone TEXT
);
CREATE TABLE IF NOT EXISTS registrations(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tournament_id INTEGER NOT NULL REFERENCES tournaments(id) ON DELETE CASCADE,
  player_discord_id TEXT NOT NULL,
  registered_at TEXT NOT NULL,
  UNIQUE(tournament_id, player_discord_id)
);
CREATE TABLE IF NOT EXISTS teams(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tournament_id INTEGER NOT NULL REFERENCES tournaments(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  created_by TEXT NOT NULL,
  UNIQUE(tournament_id, name)
);
CREATE TABLE IF NOT EXISTS team_members(
  team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
  player_discord_id TEXT NOT NULL,
  UNIQUE(team_id, player_discord_id)
);
CREATE TABLE IF NOT EXISTS tee_times(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tournament_id INTEGER NOT NULL REFERENCES tournaments(id) ON DELETE CASCADE,
  label TEXT NOT NULL,
  starts_at TEXT,
  max_players INTEGER NOT NULL DEFAULT 4,
  created_by TEXT NOT NULL,
  channel_id TEXT,
  round_number INTEGER NOT NULL DEFAULT 1,
  reminded_30 INTEGER NOT NULL DEFAULT 0,
  reminded_5 INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS tee_time_players(
  tee_time_id INTEGER NOT NULL REFERENCES tee_times(id) ON DELETE CASCADE,
  player_discord_id TEXT NOT NULL,
  UNIQUE(tee_time_id, player_discord_id)
);
CREATE TABLE IF NOT EXISTS boards(
  guild_id TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('register','teesheet')),
  channel_id TEXT NOT NULL,
  message_id TEXT NOT NULL,
  PRIMARY KEY (guild_id, kind)
);
CREATE TABLE IF NOT EXISTS scorecards(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tournament_id INTEGER NOT NULL REFERENCES tournaments(id) ON DELETE CASCADE,
  round_number INTEGER NOT NULL DEFAULT 1 CHECK(round_number BETWEEN 1 AND 5),
  player_discord_id TEXT,
  team_id INTEGER REFERENCES teams(id) ON DELETE SET NULL,
  tee_time_id INTEGER REFERENCES tee_times(id) ON DELETE SET NULL,
  holes_json TEXT NOT NULL,
  total INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','verified')),
  submitted_at TEXT NOT NULL,
  verified_by TEXT,
  submitted_by TEXT,
  witness_name TEXT
);
CREATE TABLE IF NOT EXISTS matches(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tournament_id INTEGER NOT NULL REFERENCES tournaments(id) ON DELETE CASCADE,
  player1 TEXT NOT NULL,
  player2 TEXT NOT NULL,
  winner TEXT CHECK(winner IN ('player1','player2','tie')),
  score_note TEXT,
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','confirmed')),
  reported_by TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_scorecards_tournament ON scorecards(tournament_id);
CREATE INDEX IF NOT EXISTS idx_registrations_tournament ON registrations(tournament_id);
CREATE INDEX IF NOT EXISTS idx_teetimes_tournament ON tee_times(tournament_id);
CREATE TABLE IF NOT EXISTS join_requests(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tee_time_id INTEGER NOT NULL REFERENCES tee_times(id) ON DELETE CASCADE,
  player_discord_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK(status IN ('pending','accepted','declined')),
  created_at TEXT NOT NULL,
  decided_at TEXT,
  decided_by TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_join_requests_one_pending
  ON join_requests(tee_time_id, player_discord_id) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_join_requests_tee_time ON join_requests(tee_time_id);
CREATE TABLE IF NOT EXISTS side_quests(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  guild_id TEXT NOT NULL,
  quest_group TEXT NOT NULL,
  format TEXT NOT NULL CHECK(format IN ('stroke','alt_shot','scramble')),
  course TEXT NOT NULL,
  holes INTEGER NOT NULL CHECK(holes IN (9,18)),
  player_name TEXT NOT NULL,
  scores_json TEXT NOT NULL,
  total INTEGER NOT NULL,
  logged_by TEXT NOT NULL,
  logged_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_side_quests_guild ON side_quests(guild_id);
CREATE INDEX IF NOT EXISTS idx_side_quests_group ON side_quests(quest_group);
CREATE TABLE IF NOT EXISTS seasons(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  guild_id TEXT NOT NULL,
  name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active'
    CHECK(status IN ('active','completed')),
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS season_tournaments(
  season_id INTEGER NOT NULL REFERENCES seasons(id) ON DELETE CASCADE,
  tournament_id INTEGER NOT NULL REFERENCES tournaments(id) ON DELETE CASCADE,
  UNIQUE(season_id, tournament_id)
);
CREATE TABLE IF NOT EXISTS season_points(
  season_id INTEGER NOT NULL REFERENCES seasons(id) ON DELETE CASCADE,
  tournament_id INTEGER NOT NULL REFERENCES tournaments(id) ON DELETE CASCADE,
  player_discord_id TEXT NOT NULL,
  position INTEGER NOT NULL,
  points INTEGER NOT NULL,
  UNIQUE(season_id, tournament_id, player_discord_id)
);
CREATE TABLE IF NOT EXISTS tournament_leaders(
  tournament_id INTEGER PRIMARY KEY REFERENCES tournaments(id) ON DELETE CASCADE,
  leader_key TEXT NOT NULL,
  leader_sort TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_seasons_guild ON seasons(guild_id);
CREATE INDEX IF NOT EXISTS idx_season_points_season ON season_points(season_id);
CREATE TABLE IF NOT EXISTS outbox(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  payload TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_outbox_kind ON outbox(kind);
"""


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def init_db(db_path: str) -> None:
    async with aiosqlite.connect(db_path) as con:
        await con.executescript(SCHEMA)
        await con.commit()
    await _migrate(db_path)


async def _migrate(db_path: str) -> None:
    """Bring pre-existing databases up to the current schema.

    - Adds tournaments.start_date / end_date when missing.
    - Adds tournaments.tee_position / pin_position / wind_strength /
      green_speed when missing (NOT NULL with the same defaults as a
      fresh schema).
    - Rebuilds the tournaments table when its format CHECK predates
      'scramble' (SQLite can't ALTER a CHECK constraint, so the table is
      copied into the new shape — all rows are preserved).
    - Adds players.golfplus_handle / timezone when missing.
    - Adds scorecards.submitted_by when missing.
    New tables (join_requests, side_quests) are handled by the idempotent
    CREATE TABLE IF NOT EXISTS in SCHEMA.
    """
    async with aiosqlite.connect(db_path) as con:
        cur = await con.execute("PRAGMA table_info(tournaments)")
        cols = [r[1] for r in await cur.fetchall()]
        if "start_date" not in cols:
            await con.execute("ALTER TABLE tournaments ADD COLUMN start_date TEXT")
        if "end_date" not in cols:
            await con.execute("ALTER TABLE tournaments ADD COLUMN end_date TEXT")
        for col, default in (("tee_position", "'middle'"),
                             ("pin_position", "'white'"),
                             ("wind_strength", "'moderate'"),
                             ("green_speed", "'pro'")):
            if col not in cols:
                await con.execute(
                    f"ALTER TABLE tournaments ADD COLUMN {col} "
                    f"TEXT NOT NULL DEFAULT {default}"
                )
        await con.commit()

        cur = await con.execute("PRAGMA table_info(players)")
        player_cols = [r[1] for r in await cur.fetchall()]
        if "golfplus_handle" not in player_cols:
            await con.execute("ALTER TABLE players ADD COLUMN golfplus_handle TEXT")
            await con.commit()
        if "timezone" not in player_cols:
            await con.execute("ALTER TABLE players ADD COLUMN timezone TEXT")
            await con.commit()

        cur = await con.execute("PRAGMA table_info(scorecards)")
        card_cols = [r[1] for r in await cur.fetchall()]
        if "submitted_by" not in card_cols:
            await con.execute("ALTER TABLE scorecards ADD COLUMN submitted_by TEXT")
            await con.commit()
        if "round_number" not in card_cols:
            await con.execute(
                "ALTER TABLE scorecards ADD COLUMN round_number INTEGER"
                " NOT NULL DEFAULT 1"
            )
            await con.commit()
        if "witness_name" not in card_cols:
            await con.execute(
                "ALTER TABLE scorecards ADD COLUMN witness_name TEXT")
            await con.commit()

        # tee_times.round_number (defaults to 1 for existing tee times).
        cur = await con.execute("PRAGMA table_info(tee_times)")
        tt_cols = [r[1] for r in await cur.fetchall()]
        if "round_number" not in tt_cols:
            await con.execute(
                "ALTER TABLE tee_times ADD COLUMN round_number INTEGER"
                " NOT NULL DEFAULT 1"
            )
            await con.commit()

        # rounds.start_date / end_date. Existing rounds inherit their
        # tournament's overall window until crew tunes them.
        cur = await con.execute("PRAGMA table_info(rounds)")
        round_cols = [r[1] for r in await cur.fetchall()]
        for col in ("start_date", "end_date"):
            if col not in round_cols:
                await con.execute(
                    f"ALTER TABLE rounds ADD COLUMN {col} TEXT")
                await con.commit()
        await con.execute(
            "UPDATE rounds SET start_date = "
            " (SELECT start_date FROM tournaments t"
            "  WHERE t.id = rounds.tournament_id)"
            " WHERE start_date IS NULL"
        )
        await con.execute(
            "UPDATE rounds SET end_date = "
            " (SELECT end_date FROM tournaments t"
            "  WHERE t.id = rounds.tournament_id)"
            " WHERE end_date IS NULL"
        )
        await con.commit()

        # Every tournament gets at least round 1 (copies the tournament's
        # own tee/pin/wind and overall dates). The rounds table itself is
        # created by SCHEMA.
        cur = await con.execute(
            "SELECT id, tee_position, pin_position, wind_strength,"
            " start_date, end_date"
            " FROM tournaments t WHERE NOT EXISTS"
            " (SELECT 1 FROM rounds r WHERE r.tournament_id = t.id)"
        )
        for tid, tee, pin, wind, sdate, edate in await cur.fetchall():
            await con.execute(
                "INSERT INTO rounds (tournament_id, round_number,"
                " tee_position, pin_position, wind_strength,"
                " start_date, end_date)"
                " VALUES (?,?,?,?,?,?,?)",
                (tid, 1, tee or "middle", pin or "white",
                 wind or "moderate", sdate, edate),
            )
        await con.commit()

        cur = await con.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'tournaments'"
        )
        row = await cur.fetchone()
        table_sql = row[0] if row else ""
        if "'scramble'" not in table_sql:
            # Rebuild with the new CHECK constraint, preserving every row.
            # FK enforcement is off in this app (legacy SQLite behavior), so
            # child tables referencing tournaments(id) are unaffected.
            await con.execute(
                """CREATE TABLE tournaments_new(
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  guild_id TEXT NOT NULL,
                  name TEXT NOT NULL,
                  format TEXT NOT NULL CHECK(format IN ('stroke','match','best_ball','alt_shot','scramble')),
                  holes INTEGER NOT NULL CHECK(holes IN (9,18)),
                  course TEXT NOT NULL,
                  pars TEXT,
                  description TEXT,
                  status TEXT NOT NULL DEFAULT 'registration_open'
                    CHECK(status IN ('registration_open','in_progress','completed')),
                  created_by TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  leaderboard_channel_id TEXT,
                  leaderboard_message_id TEXT,
                  start_date TEXT,
                  end_date TEXT,
                  tee_position TEXT NOT NULL DEFAULT 'middle'
                    CHECK(tee_position IN ('front','middle','back')),
                  pin_position TEXT NOT NULL DEFAULT 'white'
                    CHECK(pin_position IN ('black','white','red')),
                  wind_strength TEXT NOT NULL DEFAULT 'moderate'
                    CHECK(wind_strength IN ('low','moderate','severe')),
                  green_speed TEXT NOT NULL DEFAULT 'pro'
                    CHECK(green_speed IN ('veryfast','pro'))
                )"""
            )
            await con.execute(
                """INSERT INTO tournaments_new
                  (id, guild_id, name, format, holes, course, pars, description,
                   status, created_by, created_at, leaderboard_channel_id,
                   leaderboard_message_id, start_date, end_date,
                   tee_position, pin_position, wind_strength, green_speed)
                SELECT id, guild_id, name, format, holes, course, pars, description,
                   status, created_by, created_at, leaderboard_channel_id,
                   leaderboard_message_id, start_date, end_date,
                   tee_position, pin_position, wind_strength, green_speed
                FROM tournaments"""
            )
            await con.execute("DROP TABLE tournaments")
            await con.execute("ALTER TABLE tournaments_new RENAME TO tournaments")
            await con.commit()


def _dicts(rows) -> list[dict]:
    return [dict(r) for r in rows]


async def _fetchall(db_path: str, sql: str, params: tuple = ()) -> list[dict]:
    async with aiosqlite.connect(db_path) as con:
        con.row_factory = aiosqlite.Row
        async with con.execute(sql, params) as cur:
            return _dicts(await cur.fetchall())


async def _fetchone(db_path: str, sql: str, params: tuple = ()) -> dict | None:
    async with aiosqlite.connect(db_path) as con:
        con.row_factory = aiosqlite.Row
        async with con.execute(sql, params) as cur:
            row = await cur.fetchone()
            return dict(row) if row is not None else None


async def _execute(db_path: str, sql: str, params: tuple = ()) -> tuple[int, int]:
    """Returns (lastrowid, rowcount)."""
    async with aiosqlite.connect(db_path) as con:
        cur = await con.execute(sql, params)
        await con.commit()
        return cur.lastrowid, cur.rowcount


# ---------------------------------------------------------------- tournaments
async def create_tournament(db_path, guild_id, name, format, holes, course,
                            pars, description, created_by,
                            start_date=None, end_date=None,
                            tee_position="middle", pin_position="white",
                            wind_strength="moderate",
                            green_speed="pro", rounds=None) -> int:
    """Create a tournament plus its rounds.

    ``rounds`` is an optional list of dicts with tee_position/pin_position/
    wind_strength/start_date/end_date keys (round numbers implied by order,
    1-based). When omitted, a single round copies the tournament-level
    settings. Round dates left blank default to an even split of the
    tournament's overall date range.
    """
    lastrowid, _ = await _execute(
        db_path,
        "INSERT INTO tournaments (guild_id, name, format, holes, course, pars,"
        " description, status, created_by, created_at, start_date, end_date,"
        " tee_position, pin_position, wind_strength, green_speed)"
        " VALUES (?,?,?,?,?,?,?, 'registration_open', ?, ?, ?, ?, ?, ?, ?, ?)",
        (guild_id, name, format, holes, course, pars, description,
         created_by, utcnow_iso(), start_date, end_date,
         tee_position, pin_position, wind_strength, green_speed),
    )
    if not rounds:
        rounds = [{"tee_position": tee_position,
                   "pin_position": pin_position,
                   "wind_strength": wind_strength}]
    default_dates = _split_date_range(start_date, end_date, len(rounds))
    for i, r in enumerate(rounds, start=1):
        dflt_start, dflt_end = default_dates[i - 1]
        await create_round(
            db_path, lastrowid, i,
            r.get("tee_position") or "middle",
            r.get("pin_position") or "white",
            r.get("wind_strength") or "moderate",
            start_date=r.get("start_date") or dflt_start,
            end_date=r.get("end_date") or dflt_end,
        )
    return lastrowid


def _split_date_range(start_s: str | None, end_s: str | None,
                      n: int) -> list[tuple[str | None, str | None]]:
    """Evenly split a YYYY-MM-DD range into n (start, end) date slices.

    Used as the default per-round window: round 1 gets the first slice,
    round n the last. With unparseable/missing dates every slice is None.
    """
    try:
        from datetime import date
        start = date.fromisoformat(start_s) if start_s else None
        end = date.fromisoformat(end_s) if end_s else None
        if start is None or end is None or n < 1:
            raise ValueError
    except (ValueError, TypeError):
        return [(None, None)] * max(n, 1)
    total_days = (end - start).days + 1
    out = []
    for i in range(n):
        s = start + timedelta(days=(total_days * i) // n)
        e = start + timedelta(days=(total_days * (i + 1)) // n) - timedelta(days=1)
        if e < s:
            e = s
        out.append((s.isoformat(), e.isoformat()))
    return out


async def create_round(db_path, tournament_id: int, round_number: int,
                       tee_position: str = "middle",
                       pin_position: str = "white",
                       wind_strength: str = "moderate",
                       start_date: str | None = None,
                       end_date: str | None = None) -> int:
    lastrowid, _ = await _execute(
        db_path,
        "INSERT INTO rounds (tournament_id, round_number,"
        " tee_position, pin_position, wind_strength, start_date, end_date)"
        " VALUES (?,?,?,?,?,?,?)"
        " ON CONFLICT(tournament_id, round_number) DO UPDATE SET"
        " tee_position = excluded.tee_position,"
        " pin_position = excluded.pin_position,"
        " wind_strength = excluded.wind_strength,"
        " start_date = excluded.start_date,"
        " end_date = excluded.end_date",
        (tournament_id, round_number, tee_position, pin_position,
         wind_strength, start_date, end_date),
    )
    return lastrowid


async def list_rounds(db_path, tournament_id: int) -> list[dict]:
    """Rounds for a tournament, ordered by round_number (never empty)."""
    rows = await _fetchall(
        db_path,
        "SELECT * FROM rounds WHERE tournament_id = ? ORDER BY round_number",
        (tournament_id,),
    )
    if rows:
        return rows
    # Defensive: tournaments predating the rounds table.
    t = await get_tournament(db_path, tournament_id)
    if t is None:
        return []
    await create_round(db_path, tournament_id, 1,
                       t.get("tee_position") or "middle",
                       t.get("pin_position") or "white",
                       t.get("wind_strength") or "moderate",
                       start_date=t.get("start_date"),
                       end_date=t.get("end_date"))
    return await _fetchall(
        db_path,
        "SELECT * FROM rounds WHERE tournament_id = ? ORDER BY round_number",
        (tournament_id,),
    )


async def get_round(db_path, tournament_id: int,
                    round_number: int) -> dict | None:
    return await _fetchone(
        db_path,
        "SELECT * FROM rounds WHERE tournament_id = ? AND round_number = ?",
        (tournament_id, round_number),
    )


async def update_round(db_path, tournament_id: int, round_number: int,
                       tee_position: str | None = None,
                       pin_position: str | None = None,
                       wind_strength: str | None = None,
                       start_date: str | None = None,
                       end_date: str | None = None) -> dict | None:
    """Patch a round's settings/dates; returns the updated round (or None)."""
    rnd = await get_round(db_path, tournament_id, round_number)
    if rnd is None:
        return None
    await _execute(
        db_path,
        "UPDATE rounds SET tee_position = ?, pin_position = ?,"
        " wind_strength = ?, start_date = ?, end_date = ?"
        " WHERE tournament_id = ? AND round_number = ?",
        (tee_position or rnd["tee_position"],
         pin_position or rnd["pin_position"],
         wind_strength or rnd["wind_strength"],
         start_date or rnd["start_date"],
         end_date or rnd["end_date"],
         tournament_id, round_number),
    )
    return await get_round(db_path, tournament_id, round_number)


async def get_tournament(db_path, tournament_id) -> dict | None:
    return await _fetchone(db_path, "SELECT * FROM tournaments WHERE id = ?",
                           (tournament_id,))


# ------------------------------------------------------------------- boards
async def get_board(db_path, guild_id, kind) -> dict | None:
    """Recorded location of a persistent board message ('register'/'teesheet')."""
    return await _fetchone(
        db_path, "SELECT * FROM boards WHERE guild_id = ? AND kind = ?",
        (guild_id, kind),
    )


async def set_board(db_path, guild_id, kind, channel_id, message_id) -> None:
    await _execute(
        db_path,
        "INSERT INTO boards (guild_id, kind, channel_id, message_id)"
        " VALUES (?,?,?,?)"
        " ON CONFLICT(guild_id, kind) DO UPDATE SET"
        " channel_id = excluded.channel_id, message_id = excluded.message_id",
        (guild_id, kind, str(channel_id), str(message_id)),
    )


async def list_tournaments(db_path, guild_id, statuses=None, search=None) -> list[dict]:
    sql = "SELECT * FROM tournaments WHERE guild_id = ?"
    params: list = [guild_id]
    if statuses:
        sql += " AND status IN (%s)" % ",".join("?" for _ in statuses)
        params.extend(statuses)
    if search:
        sql += " AND name LIKE ?"
        params.append(f"%{search}%")
    # Open tournaments first, then live ones, then completed; within each
    # group the soonest-starting (dated) tournaments come first. This keeps
    # the registration list view useful when 2+ events run side by side.
    sql += (" ORDER BY CASE status WHEN 'registration_open' THEN 0"
            " WHEN 'in_progress' THEN 1 ELSE 2 END,"
            " start_date IS NULL, start_date ASC, id DESC")
    return await _fetchall(db_path, sql, tuple(params))


async def set_tournament_status(db_path, tournament_id, status) -> None:
    await _execute(db_path, "UPDATE tournaments SET status = ? WHERE id = ?",
                   (status, tournament_id))


async def set_leaderboard_message(db_path, tournament_id, channel_id, message_id) -> None:
    await _execute(
        db_path,
        "UPDATE tournaments SET leaderboard_channel_id = ?,"
        " leaderboard_message_id = ? WHERE id = ?",
        (str(channel_id), str(message_id), tournament_id),
    )


async def list_tournaments_by_status(db_path, statuses) -> list[dict]:
    """All guilds — used to re-attach persistent views after a restart."""
    sql = "SELECT * FROM tournaments WHERE status IN (%s) ORDER BY id DESC" % ",".join(
        "?" for _ in statuses
    )
    return await _fetchall(db_path, sql, tuple(statuses))


# ------------------------------------------------------------------- players
async def upsert_player(db_path, discord_id, display_name) -> None:
    await _execute(
        db_path,
        "INSERT INTO players (discord_id, display_name) VALUES (?, ?)"
        " ON CONFLICT(discord_id) DO UPDATE SET display_name = excluded.display_name",
        (discord_id, display_name),
    )


async def get_player(db_path, discord_id) -> dict | None:
    return await _fetchone(db_path, "SELECT * FROM players WHERE discord_id = ?",
                           (discord_id,))


def display_name_of(player_row: dict | None, discord_id: str) -> str:
    if player_row and player_row.get("display_name"):
        name = player_row["display_name"]
    else:
        return f"<@{discord_id}>"
    handle = (player_row.get("golfplus_handle") or "").strip()
    if handle:
        name = f"{name} (Golf+: {handle})"
    return name


async def set_golfplus_handle(db_path, discord_id: str, handle: str | None) -> None:
    """Link (or, with None, unlink) a player's Golf+ username."""
    await _execute(
        db_path,
        "UPDATE players SET golfplus_handle = ? WHERE discord_id = ?",
        (handle, discord_id),
    )


async def set_timezone(db_path, discord_id: str, tz_name: str | None) -> None:
    """Store a player's IANA timezone (e.g. 'America/Denver')."""
    await _execute(
        db_path,
        "UPDATE players SET timezone = ? WHERE discord_id = ?",
        (tz_name, discord_id),
    )


async def get_timezone(db_path, discord_id: str) -> str | None:
    """A player's stored IANA timezone, or None when never set."""
    row = await _fetchone(
        db_path, "SELECT timezone FROM players WHERE discord_id = ?",
        (discord_id,),
    )
    return (row or {}).get("timezone") or None


# ------------------------------------------------------------- registrations
async def register_player(db_path, tournament_id, discord_id) -> bool:
    """Returns True if this is a new registration, False if already registered."""
    _, rowcount = await _execute(
        db_path,
        "INSERT OR IGNORE INTO registrations (tournament_id, player_discord_id,"
        " registered_at) VALUES (?,?,?)",
        (tournament_id, discord_id, utcnow_iso()),
    )
    return rowcount == 1


async def unregister_player(db_path, tournament_id, discord_id) -> bool:
    _, rowcount = await _execute(
        db_path,
        "DELETE FROM registrations WHERE tournament_id = ? AND player_discord_id = ?",
        (tournament_id, discord_id),
    )
    return rowcount > 0


async def is_registered(db_path, tournament_id, discord_id) -> bool:
    row = await _fetchone(
        db_path,
        "SELECT 1 FROM registrations WHERE tournament_id = ? AND player_discord_id = ?",
        (tournament_id, discord_id),
    )
    return row is not None


async def get_roster(db_path, tournament_id) -> list[dict]:
    return await _fetchall(
        db_path,
        "SELECT r.player_discord_id AS discord_id,"
        " COALESCE(p.display_name, r.player_discord_id) AS display_name,"
        " p.golfplus_handle,"
        " r.registered_at FROM registrations r"
        " LEFT JOIN players p ON p.discord_id = r.player_discord_id"
        " WHERE r.tournament_id = ? ORDER BY r.registered_at ASC",
        (tournament_id,),
    )


# --------------------------------------------------------------------- teams
async def create_team(db_path, tournament_id, name, created_by) -> int:
    lastrowid, _ = await _execute(
        db_path,
        "INSERT INTO teams (tournament_id, name, created_by) VALUES (?,?,?)",
        (tournament_id, name, created_by),
    )
    return lastrowid


async def get_team(db_path, team_id) -> dict | None:
    return await _fetchone(db_path, "SELECT * FROM teams WHERE id = ?", (team_id,))


async def get_team_by_name(db_path, tournament_id, name) -> dict | None:
    return await _fetchone(
        db_path,
        "SELECT * FROM teams WHERE tournament_id = ? AND name = ? COLLATE NOCASE",
        (tournament_id, name),
    )


async def get_teams(db_path, tournament_id) -> list[dict]:
    return await _fetchall(
        db_path, "SELECT * FROM teams WHERE tournament_id = ? ORDER BY name ASC",
        (tournament_id,),
    )


async def add_team_member(db_path, team_id, discord_id) -> bool:
    _, rowcount = await _execute(
        db_path,
        "INSERT OR IGNORE INTO team_members (team_id, player_discord_id) VALUES (?,?)",
        (team_id, discord_id),
    )
    return rowcount == 1


async def remove_team_member(db_path, team_id, discord_id) -> bool:
    _, rowcount = await _execute(
        db_path,
        "DELETE FROM team_members WHERE team_id = ? AND player_discord_id = ?",
        (team_id, discord_id),
    )
    return rowcount > 0


async def get_team_members(db_path, team_id) -> list[dict]:
    return await _fetchall(
        db_path,
        "SELECT tm.player_discord_id AS discord_id,"
        " COALESCE(p.display_name, tm.player_discord_id) AS display_name"
        " FROM team_members tm LEFT JOIN players p ON p.discord_id = tm.player_discord_id"
        " WHERE tm.team_id = ? ORDER BY tm.player_discord_id ASC",
        (team_id,),
    )


async def get_player_teams(db_path, tournament_id, discord_id) -> list[dict]:
    return await _fetchall(
        db_path,
        "SELECT t.* FROM teams t JOIN team_members tm ON tm.team_id = t.id"
        " WHERE t.tournament_id = ? AND tm.player_discord_id = ?",
        (tournament_id, discord_id),
    )


# ----------------------------------------------------------------- tee times
async def create_tee_time(db_path, tournament_id, label, starts_at, max_players,
                          created_by, channel_id, round_number: int = 1) -> int:
    round_number = max(1, min(5, int(round_number or 1)))
    lastrowid, _ = await _execute(
        db_path,
        "INSERT INTO tee_times (tournament_id, label, starts_at, max_players,"
        " created_by, channel_id, round_number) VALUES (?,?,?,?,?,?,?)",
        (tournament_id, label, starts_at, max_players, created_by, channel_id,
         round_number),
    )
    return lastrowid


async def get_tee_time(db_path, tee_time_id) -> dict | None:
    return await _fetchone(db_path, "SELECT * FROM tee_times WHERE id = ?",
                           (tee_time_id,))


async def list_tee_times(db_path, tournament_id) -> list[dict]:
    return await _fetchall(
        db_path,
        "SELECT tt.*, (SELECT COUNT(*) FROM tee_time_players tp"
        " WHERE tp.tee_time_id = tt.id) AS player_count"
        " FROM tee_times tt WHERE tt.tournament_id = ?"
        " ORDER BY tt.starts_at ASC, tt.id ASC",
        (tournament_id,),
    )


async def update_tee_time(db_path, tee_time_id, *, label=None,
                          starts_at=None, round_number=None) -> bool:
    """Update a tee time's label, start time (ISO string) and/or round.

    Returns True when a row was actually changed.
    """
    sets, params = [], []
    if label is not None:
        sets.append("label = ?")
        params.append(label)
    if starts_at is not None:
        sets.append("starts_at = ?")
        params.append(starts_at)
    if round_number is not None:
        sets.append("round_number = ?")
        params.append(max(1, min(5, int(round_number))))
    if not sets:
        return False
    params.append(tee_time_id)
    _, rowcount = await _execute(
        db_path,
        "UPDATE tee_times SET %s WHERE id = ?" % ", ".join(sets),
        tuple(params),
    )
    return rowcount > 0


async def count_tee_time_scorecards(db_path, tee_time_id) -> int:
    row = await _fetchone(
        db_path,
        "SELECT COUNT(*) AS c FROM scorecards WHERE tee_time_id = ?",
        (tee_time_id,),
    )
    return int(row["c"]) if row else 0


async def delete_tee_time(db_path, tee_time_id) -> None:
    """Delete a tee time plus its players and join requests.

    No FK enforcement in this DB, so cascade manually. Callers should
    refuse when scorecards exist (count_tee_time_scorecards) rather than
    orphaning submitted scores.
    """
    await _execute(
        db_path, "DELETE FROM tee_time_players WHERE tee_time_id = ?",
        (tee_time_id,))
    await _execute(
        db_path, "DELETE FROM join_requests WHERE tee_time_id = ?",
        (tee_time_id,))
    await _execute(
        db_path, "DELETE FROM tee_times WHERE id = ?", (tee_time_id,))


async def update_tournament(db_path, tournament_id, *, name=None,
                            description=None, start_date=None, end_date=None,
                            course=None) -> bool:
    """Update safe tournament fields (never format/holes/rounds — those
    would corrupt existing scorecards). Returns True when a row changed."""
    sets, params = [], []
    if name is not None:
        sets.append("name = ?")
        params.append(name)
    if description is not None:
        sets.append("description = ?")
        params.append(description)
    if start_date is not None:
        sets.append("start_date = ?")
        params.append(start_date)
    if end_date is not None:
        sets.append("end_date = ?")
        params.append(end_date)
    if course is not None:
        sets.append("course = ?")
        params.append(course)
    if not sets:
        return False
    params.append(tournament_id)
    _, rowcount = await _execute(
        db_path,
        "UPDATE tournaments SET %s WHERE id = ?" % ", ".join(sets),
        tuple(params),
    )
    return rowcount > 0


async def tournament_usage_counts(db_path, tournament_id) -> dict:
    """Counts for the delete confirmation prompt."""
    out = {}
    for key, table, col in [
        ("registrations", "registrations", "tournament_id"),
        ("tee_times", "tee_times", "tournament_id"),
        ("scorecards", "scorecards", "tournament_id"),
    ]:
        row = await _fetchone(
            db_path,
            f"SELECT COUNT(*) AS c FROM {table} WHERE {col} = ?",
            (tournament_id,),
        )
        out[key] = int(row["c"]) if row else 0
    return out


async def delete_tournament(db_path, tournament_id) -> None:
    """Delete a tournament and everything under it.

    No FK enforcement in this DB, so cascade manually, children first.
    Pending outbox rows for the tournament are left alone — the drain
    skips (and acks) events whose tournament no longer exists.
    """
    tt_rows = await _fetchall(
        db_path, "SELECT id FROM tee_times WHERE tournament_id = ?",
        (tournament_id,))
    for r in tt_rows:
        await delete_tee_time(db_path, r["id"])
    for table in ("scorecards", "matches", "tournament_leaders",
                  "season_points", "season_tournaments", "registrations",
                  "teams", "rounds"):
        await _execute(
            db_path, f"DELETE FROM {table} WHERE tournament_id = ?",
            (tournament_id,))
    await _execute(
        db_path, "DELETE FROM tournaments WHERE id = ?", (tournament_id,))


async def search_tee_times(db_path, guild_id, current) -> list[dict]:
    sql = ("SELECT tt.*, t.name AS tournament_name FROM tee_times tt"
           " JOIN tournaments t ON t.id = tt.tournament_id"
           " WHERE t.guild_id = ? AND t.status IN ('registration_open','in_progress')")
    params: list = [guild_id]
    if current:
        sql += " AND (tt.label LIKE ? OR t.name LIKE ?)"
        params.extend([f"%{current}%", f"%{current}%"])
    sql += " ORDER BY tt.starts_at ASC LIMIT 25"
    return await _fetchall(db_path, sql, tuple(params))


async def get_tee_time_players(db_path, tee_time_id) -> list[dict]:
    return await _fetchall(
        db_path,
        "SELECT tp.player_discord_id AS discord_id,"
        " COALESCE(p.display_name, tp.player_discord_id) AS display_name,"
        " p.golfplus_handle"
        " FROM tee_time_players tp LEFT JOIN players p ON p.discord_id = tp.player_discord_id"
        " WHERE tp.tee_time_id = ?",
        (tee_time_id,),
    )


async def tee_time_player_count(db_path, tee_time_id) -> int:
    row = await _fetchone(
        db_path, "SELECT COUNT(*) AS c FROM tee_time_players WHERE tee_time_id = ?",
        (tee_time_id,),
    )
    return row["c"] if row else 0


async def join_tee_time(db_path, tee_time_id, discord_id) -> str:
    """Returns 'ok', 'full', or 'already'."""
    tt = await get_tee_time(db_path, tee_time_id)
    if tt is None:
        return "missing"
    players = await get_tee_time_players(db_path, tee_time_id)
    if any(p["discord_id"] == discord_id for p in players):
        return "already"
    if len(players) >= (tt["max_players"] or 4):
        return "full"
    await _execute(
        db_path,
        "INSERT OR IGNORE INTO tee_time_players (tee_time_id, player_discord_id)"
        " VALUES (?,?)",
        (tee_time_id, discord_id),
    )
    return "ok"


async def leave_tee_time(db_path, tee_time_id, discord_id) -> bool:
    _, rowcount = await _execute(
        db_path,
        "DELETE FROM tee_time_players WHERE tee_time_id = ? AND player_discord_id = ?",
        (tee_time_id, discord_id),
    )
    return rowcount > 0


async def get_player_tee_time(db_path, tournament_id, discord_id) -> dict | None:
    return await _fetchone(
        db_path,
        "SELECT tt.* FROM tee_times tt JOIN tee_time_players tp ON tp.tee_time_id = tt.id"
        " WHERE tt.tournament_id = ? AND tp.player_discord_id = ? LIMIT 1",
        (tournament_id, discord_id),
    )


async def get_reminder_due(db_path, now_iso: str, horizon_iso: str) -> list[dict]:
    """Tee times starting after now but within the horizon.

    Both bounds are Python-generated ISO-8601 strings in identical format, so
    lexicographic comparison is chronological (avoids SQLite datetime() which
    normalizes to a different string format).
    """
    return await _fetchall(
        db_path,
        "SELECT * FROM tee_times WHERE starts_at IS NOT NULL AND starts_at > ?"
        " AND starts_at <= ? AND (reminded_30 = 0 OR reminded_5 = 0)",
        (now_iso, horizon_iso),
    )


async def mark_reminded(db_path, tee_time_id, kind: str) -> None:
    col = "reminded_30" if kind == "30" else "reminded_5"
    await _execute(db_path, f"UPDATE tee_times SET {col} = 1 WHERE id = ?",
                   (tee_time_id,))
    # NOTE: column name is chosen from a fixed pair above, never user input.


# ------------------------------------------------------------ join requests
async def create_join_request(db_path, tee_time_id, discord_id) -> str:
    """Create a PENDING join request for someone else's tee time.

    Returns 'ok', 'pending' (already has a pending request for this tee
    time), 'already' (already in the tee time), 'full' (tee time is full),
    or 'missing' (no such tee time).
    """
    tt = await get_tee_time(db_path, tee_time_id)
    if tt is None:
        return "missing"
    players = await get_tee_time_players(db_path, tee_time_id)
    if any(p["discord_id"] == discord_id for p in players):
        return "already"
    if len(players) >= (tt["max_players"] or 4):
        return "full"
    existing = await _fetchone(
        db_path,
        "SELECT id FROM join_requests WHERE tee_time_id = ? AND player_discord_id = ?"
        " AND status = 'pending'",
        (tee_time_id, discord_id),
    )
    if existing:
        return "pending"
    try:
        await _execute(
            db_path,
            "INSERT INTO join_requests (tee_time_id, player_discord_id, status, created_at)"
            " VALUES (?, ?, 'pending', ?)",
            (tee_time_id, discord_id, utcnow_iso()),
        )
    except Exception:
        # Partial unique index race: treat as already pending.
        return "pending"
    return "ok"


async def get_join_request(db_path, request_id) -> dict | None:
    return await _fetchone(db_path, "SELECT * FROM join_requests WHERE id = ?",
                           (request_id,))


async def get_pending_request(db_path, tee_time_id, discord_id) -> dict | None:
    return await _fetchone(
        db_path,
        "SELECT * FROM join_requests WHERE tee_time_id = ? AND player_discord_id = ?"
        " AND status = 'pending'",
        (tee_time_id, discord_id),
    )


async def list_pending_join_requests(db_path, tee_time_id=None) -> list[dict]:
    """Pending requests; all guilds when tee_time_id is None (used to
    re-attach persistent Accept/Decline buttons after a restart)."""
    sql = ("SELECT jr.*, tt.tournament_id, tt.label AS tee_time_label,"
           " tt.created_by AS tee_time_creator, tt.channel_id"
           " FROM join_requests jr JOIN tee_times tt ON tt.id = jr.tee_time_id"
           " WHERE jr.status = 'pending'")
    params: list = []
    if tee_time_id is not None:
        sql += " AND jr.tee_time_id = ?"
        params.append(tee_time_id)
    sql += " ORDER BY jr.created_at ASC"
    return await _fetchall(db_path, sql, tuple(params))


async def decide_join_request(db_path, request_id, decision, decided_by) -> dict | None:
    """Mark a request 'accepted' or 'declined'. Returns the updated row.

    Only transitions pending requests; returns None when the request is
    missing or already decided.
    """
    req = await get_join_request(db_path, request_id)
    if req is None or req["status"] != "pending":
        return None
    if decision not in ("accepted", "declined"):
        raise ValueError("decision must be 'accepted' or 'declined'")
    await _execute(
        db_path,
        "UPDATE join_requests SET status = ?, decided_at = ?, decided_by = ?"
        " WHERE id = ? AND status = 'pending'",
        (decision, utcnow_iso(), decided_by, request_id),
    )
    return await get_join_request(db_path, request_id)


async def expire_join_requests_for_tournament(db_path, tournament_id) -> int:
    """Decline all pending join requests on a tournament's tee times.

    Called when a tournament completes so stale requests can't linger.
    Returns the number of requests expired.
    """
    _, rowcount = await _execute(
        db_path,
        "UPDATE join_requests SET status = 'declined', decided_at = ?"
        " WHERE status = 'pending' AND tee_time_id IN"
        " (SELECT id FROM tee_times WHERE tournament_id = ?)",
        (utcnow_iso(), tournament_id),
    )
    return rowcount


# -------------------------------------------------------------- side quests
async def log_side_quest(db_path, guild_id, quest_group, format, course, holes,
                         entries: list[tuple[str, list[int]]], logged_by) -> str:
    """Log one casual round. entries = [(player_name, scores), ...].

    One row per player-round; all rows share quest_group so a round stays
    together. Returns the quest_group id.
    """
    logged_at = utcnow_iso()
    for player_name, scores in entries:
        await _execute(
            db_path,
            "INSERT INTO side_quests (guild_id, quest_group, format, course, holes,"
            " player_name, scores_json, total, logged_by, logged_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (guild_id, quest_group, format, course, holes, player_name,
             json.dumps(scores), sum(scores), logged_by, logged_at),
        )
    return quest_group


async def side_quest_bests(db_path, guild_id, player_name=None, course=None,
                           format=None) -> list[dict]:
    """Lowest total per player per format+course (personal/course bests)."""
    sql = ("SELECT format, course, player_name, MIN(total) AS best,"
           " COUNT(*) AS rounds FROM side_quests WHERE guild_id = ?")
    params: list = [guild_id]
    if player_name:
        sql += " AND player_name = ? COLLATE NOCASE"
        params.append(player_name)
    if course:
        sql += " AND course = ? COLLATE NOCASE"
        params.append(course)
    if format:
        sql += " AND format = ?"
        params.append(format)
    sql += " GROUP BY format, course, player_name ORDER BY format, course, best ASC"
    return await _fetchall(db_path, sql, tuple(params))


async def side_quest_recent(db_path, guild_id, limit=10) -> list[dict]:
    """Most recent logged rounds, grouped (one entry per round)."""
    groups = await _fetchall(
        db_path,
        "SELECT DISTINCT quest_group, format, course, holes, logged_at"
        " FROM side_quests WHERE guild_id = ? ORDER BY logged_at DESC, quest_group DESC"
        " LIMIT ?",
        (guild_id, limit),
    )
    out = []
    for g in groups:
        rows = await _fetchall(
            db_path,
            "SELECT player_name, total FROM side_quests WHERE quest_group = ?"
            " ORDER BY total ASC",
            (g["quest_group"],),
        )
        out.append({**g, "players": rows})
    return out


# ------------------------------------------------------------------- seasons
async def create_season(db_path, guild_id, name, created_by) -> int:
    lastrowid, _ = await _execute(
        db_path,
        "INSERT INTO seasons (guild_id, name, status, created_by, created_at)"
        " VALUES (?,?,'active',?,?)",
        (guild_id, name, created_by, utcnow_iso()),
    )
    return lastrowid


async def get_season(db_path, season_id) -> dict | None:
    return await _fetchone(db_path, "SELECT * FROM seasons WHERE id = ?",
                           (season_id,))


async def get_active_season(db_path, guild_id) -> dict | None:
    return await _fetchone(
        db_path,
        "SELECT * FROM seasons WHERE guild_id = ? AND status = 'active'"
        " ORDER BY id DESC LIMIT 1",
        (guild_id,),
    )


async def list_seasons(db_path, guild_id) -> list[dict]:
    return await _fetchall(
        db_path,
        "SELECT s.*, (SELECT COUNT(*) FROM season_tournaments st"
        " WHERE st.season_id = s.id) AS tournament_count"
        " FROM seasons s WHERE s.guild_id = ? ORDER BY s.id DESC",
        (guild_id,),
    )


async def complete_season(db_path, season_id) -> bool:
    _, rowcount = await _execute(
        db_path,
        "UPDATE seasons SET status = 'completed' WHERE id = ? AND status = 'active'",
        (season_id,),
    )
    return rowcount > 0


async def add_tournament_to_season(db_path, season_id, tournament_id) -> bool:
    """Link a tournament to a season. False when already linked."""
    _, rowcount = await _execute(
        db_path,
        "INSERT OR IGNORE INTO season_tournaments (season_id, tournament_id)"
        " VALUES (?,?)",
        (season_id, tournament_id),
    )
    return rowcount == 1


async def get_season_tournaments(db_path, season_id) -> list[dict]:
    return await _fetchall(
        db_path,
        "SELECT t.* FROM tournaments t JOIN season_tournaments st"
        " ON st.tournament_id = t.id WHERE st.season_id = ? ORDER BY t.id ASC",
        (season_id,),
    )


async def get_seasons_for_tournament(db_path, tournament_id,
                                     status="active") -> list[dict]:
    return await _fetchall(
        db_path,
        "SELECT s.* FROM seasons s JOIN season_tournaments st ON st.season_id = s.id"
        " WHERE st.tournament_id = ? AND s.status = ?",
        (tournament_id, status),
    )


async def record_season_points(db_path, season_id, tournament_id,
                               rows: list[tuple[str, int, int]]) -> int:
    """Insert per-player points rows: [(player_discord_id, position, points)].

    INSERT OR IGNORE makes re-awards idempotent. Returns rows inserted.
    """
    count = 0
    for player_discord_id, position, points in rows:
        _, rowcount = await _execute(
            db_path,
            "INSERT OR IGNORE INTO season_points"
            " (season_id, tournament_id, player_discord_id, position, points)"
            " VALUES (?,?,?,?,?)",
            (season_id, tournament_id, player_discord_id, position, points),
        )
        count += rowcount
    return count


async def get_season_standings(db_path, season_id) -> list[dict]:
    """Aggregate points per player: total points + tournaments played."""
    return await _fetchall(
        db_path,
        "SELECT sp.player_discord_id AS discord_id,"
        " COALESCE(p.display_name, sp.player_discord_id) AS display_name,"
        " p.golfplus_handle,"
        " SUM(sp.points) AS total_points,"
        " COUNT(DISTINCT sp.tournament_id) AS tournaments_played"
        " FROM season_points sp"
        " LEFT JOIN players p ON p.discord_id = sp.player_discord_id"
        " WHERE sp.season_id = ?"
        " GROUP BY sp.player_discord_id"
        " ORDER BY total_points DESC, tournaments_played DESC, discord_id ASC",
        (season_id,),
    )


# ------------------------------------------------------------------- leaders
async def get_leader(db_path, tournament_id) -> dict | None:
    """Stored lead-change baseline for a tournament (or None)."""
    return await _fetchone(
        db_path, "SELECT * FROM tournament_leaders WHERE tournament_id = ?",
        (tournament_id,),
    )


async def set_leader(db_path, tournament_id, leader_key, leader_sort) -> None:
    await _execute(
        db_path,
        "INSERT INTO tournament_leaders (tournament_id, leader_key, leader_sort)"
        " VALUES (?,?,?)"
        " ON CONFLICT(tournament_id) DO UPDATE SET"
        " leader_key = excluded.leader_key, leader_sort = excluded.leader_sort",
        (tournament_id, leader_key, leader_sort),
    )


# ---------------------------------------------------------------- scorecards
async def upsert_scorecard(db_path, tournament_id, player_id, team_id, tee_time_id,
                           scores: list[int], status: str,
                           submitted_by: str | None = None,
                           round_number: int = 1,
                           witness_name: str | None = None) -> int:
    holes_json = json.dumps(scores)
    total = sum(scores)
    now = utcnow_iso()
    round_number = max(1, min(5, int(round_number or 1)))
    witness_name = (witness_name or "").strip()[:80] or None
    if team_id is not None:
        existing = await _fetchone(
            db_path,
            "SELECT id FROM scorecards WHERE tournament_id = ? AND team_id = ?"
            " AND IFNULL(tee_time_id, -1) = IFNULL(?, -1)"
            " AND round_number = ?"
            " ORDER BY submitted_at DESC LIMIT 1",
            (tournament_id, team_id, tee_time_id, round_number),
        )
    else:
        existing = await _fetchone(
            db_path,
            "SELECT id FROM scorecards WHERE tournament_id = ? AND player_discord_id = ?"
            " AND IFNULL(tee_time_id, -1) = IFNULL(?, -1)"
            " AND team_id IS NULL AND round_number = ?"
            " ORDER BY submitted_at DESC LIMIT 1",
            (tournament_id, player_id, tee_time_id, round_number),
        )
    if existing:
        await _execute(
            db_path,
            "UPDATE scorecards SET holes_json = ?, total = ?, status = ?,"
            " submitted_at = ?, verified_by = NULL, submitted_by = ?,"
            " witness_name = ? WHERE id = ?",
            (holes_json, total, status, now, submitted_by, witness_name,
             existing["id"]),
        )
        return existing["id"]
    lastrowid, _ = await _execute(
        db_path,
        "INSERT INTO scorecards (tournament_id, round_number, player_discord_id,"
        " team_id, tee_time_id, holes_json, total, status, submitted_at,"
        " submitted_by, witness_name)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (tournament_id, round_number, player_id, team_id, tee_time_id,
         holes_json, total, status, now, submitted_by, witness_name),
    )
    return lastrowid


async def find_scorecard(db_path, tournament_id, *, player_discord_id=None,
                         team_id=None, tee_time_id=None,
                         round_number=1) -> dict | None:
    """Return the card an upsert_scorecard call would overwrite, or None.

    Used to enforce the crew-only edit rule: a submission with no matching
    card is a first submission (open to tee-time members); a matching card
    means an edit, which only crew may perform.
    """
    round_number = max(1, min(5, int(round_number or 1)))
    if team_id is not None:
        return await _fetchone(
            db_path,
            "SELECT * FROM scorecards WHERE tournament_id = ? AND team_id = ?"
            " AND IFNULL(tee_time_id, -1) = IFNULL(?, -1)"
            " AND round_number = ? ORDER BY submitted_at DESC LIMIT 1",
            (tournament_id, team_id, tee_time_id, round_number),
        )
    return await _fetchone(
        db_path,
        "SELECT * FROM scorecards WHERE tournament_id = ?"
        " AND player_discord_id = ?"
        " AND IFNULL(tee_time_id, -1) = IFNULL(?, -1)"
        " AND team_id IS NULL AND round_number = ?"
        " ORDER BY submitted_at DESC LIMIT 1",
        (tournament_id, player_discord_id, tee_time_id, round_number),
    )


async def get_scorecard(db_path, card_id) -> dict | None:
    return await _fetchone(db_path, "SELECT * FROM scorecards WHERE id = ?",
                           (card_id,))


async def get_scorecards(db_path, tournament_id, status=None) -> list[dict]:
    sql = "SELECT * FROM scorecards WHERE tournament_id = ?"
    params: list = [tournament_id]
    if status:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY submitted_at ASC"
    return await _fetchall(db_path, sql, tuple(params))


async def get_latest_player_card(db_path, tournament_id, discord_id,
                                 round_number: int | None = None) -> dict | None:
    sql = ("SELECT * FROM scorecards WHERE tournament_id = ?"
           " AND player_discord_id = ? AND team_id IS NULL")
    params: list = [tournament_id, discord_id]
    if round_number is not None:
        sql += " AND round_number = ?"
        params.append(round_number)
    sql += " ORDER BY submitted_at DESC LIMIT 1"
    return await _fetchone(db_path, sql, tuple(params))


async def get_latest_team_card(db_path, tournament_id, team_id,
                               round_number: int | None = None) -> dict | None:
    sql = "SELECT * FROM scorecards WHERE tournament_id = ? AND team_id = ?"
    params: list = [tournament_id, team_id]
    if round_number is not None:
        sql += " AND round_number = ?"
        params.append(round_number)
    sql += " ORDER BY submitted_at DESC LIMIT 1"
    return await _fetchone(db_path, sql, tuple(params))


async def verify_scorecard(db_path, card_id, verified_by) -> bool:
    _, rowcount = await _execute(
        db_path,
        "UPDATE scorecards SET status = 'verified', verified_by = ? WHERE id = ?",
        (verified_by, card_id),
    )
    return rowcount > 0


async def correct_scorecard_hole(db_path, card_id, hole_index: int, score: int) -> dict | None:
    """hole_index is 0-based. Returns the updated card dict, or None."""
    card = await get_scorecard(db_path, card_id)
    if card is None:
        return None
    scores = json.loads(card["holes_json"])
    if not 0 <= hole_index < len(scores):
        return None
    scores[hole_index] = score
    await _execute(
        db_path,
        "UPDATE scorecards SET holes_json = ?, total = ? WHERE id = ?",
        (json.dumps(scores), sum(scores), card_id),
    )
    return await get_scorecard(db_path, card_id)


async def delete_scorecard(db_path, card_id) -> bool:
    _, rowcount = await _execute(db_path, "DELETE FROM scorecards WHERE id = ?",
                                 (card_id,))
    return rowcount > 0


async def delete_player_cards(db_path, tournament_id, discord_id) -> int:
    _, rowcount = await _execute(
        db_path,
        "DELETE FROM scorecards WHERE tournament_id = ? AND player_discord_id = ?",
        (tournament_id, discord_id),
    )
    return rowcount


async def get_player_verified_cards(db_path, guild_id,
                                    player_discord_id) -> list[dict]:
    """Verified individual scorecards for a player across a guild's tournaments.

    Returns [{"holes_json", "pars", "holes"}]; team cards are excluded so
    personal stats stay personal.
    """
    return await _fetchall(
        db_path,
        "SELECT s.holes_json, t.pars, t.holes FROM scorecards s"
        " JOIN tournaments t ON t.id = s.tournament_id"
        " WHERE t.guild_id = ? AND s.player_discord_id = ?"
        " AND s.team_id IS NULL AND s.status = 'verified'"
        " ORDER BY s.submitted_at ASC",
        (guild_id, player_discord_id),
    )


# ------------------------------------------------------------------- matches
async def create_match(db_path, tournament_id, player1, player2, reported_by,
                       winner=None, score_note=None) -> int:
    """winner holds the reporter's *claimed* result while status='pending';
    it becomes official when the match is confirmed."""
    lastrowid, _ = await _execute(
        db_path,
        "INSERT INTO matches (tournament_id, player1, player2, winner, score_note,"
        " status, reported_by, created_at) VALUES (?,?,?,?,?,'pending',?,?)",
        (tournament_id, player1, player2, winner, score_note, reported_by,
         utcnow_iso()),
    )
    return lastrowid


async def get_match(db_path, match_id) -> dict | None:
    return await _fetchone(db_path, "SELECT * FROM matches WHERE id = ?",
                           (match_id,))


async def confirm_match(db_path, match_id, winner) -> bool:
    _, rowcount = await _execute(
        db_path,
        "UPDATE matches SET winner = ?, status = 'confirmed' WHERE id = ?",
        (winner, match_id),
    )
    return rowcount > 0


async def list_matches(db_path, tournament_id, status=None) -> list[dict]:
    sql = "SELECT * FROM matches WHERE tournament_id = ?"
    params: list = [tournament_id]
    if status:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY id DESC"
    return await _fetchall(db_path, sql, tuple(params))


async def list_pending_matches(db_path) -> list[dict]:
    """All guilds — used to re-attach persistent confirm buttons after a restart."""
    return await _fetchall(
        db_path, "SELECT * FROM matches WHERE status = 'pending' ORDER BY id DESC"
    )


async def list_confirmed_matches_for_player(db_path, guild_id,
                                            discord_id) -> list[dict]:
    """Confirmed matches involving a player, across a guild's tournaments."""
    return await _fetchall(
        db_path,
        "SELECT m.* FROM matches m JOIN tournaments t ON t.id = m.tournament_id"
        " WHERE t.guild_id = ? AND m.status = 'confirmed'"
        " AND (m.player1 = ? OR m.player2 = ?)",
        (guild_id, discord_id, discord_id),
    )


# ------------------------------------------------------------------- outbox
# Cross-process work queue: the API (which never touches Discord) enqueues
# events here; the bot drains them on a timer and performs the Discord-side
# effects (persistent views, board refreshes). Rows are deleted only after
# successful handling, so a crash retries them — handlers must be idempotent.
async def enqueue_outbox(db_path, kind: str, payload: dict) -> int:
    lastrowid, _ = await _execute(
        db_path,
        "INSERT INTO outbox (kind, payload, created_at) VALUES (?,?,?)",
        (kind, json.dumps(payload), utcnow_iso()),
    )
    return lastrowid


async def poll_outbox(db_path, limit: int = 25) -> list[dict]:
    rows = await _fetchall(
        db_path, "SELECT * FROM outbox ORDER BY id ASC LIMIT ?", (limit,)
    )
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["payload"] = json.loads(d.get("payload") or "{}")
        except (ValueError, TypeError):
            d["payload"] = {}
        out.append(d)
    return out


async def ack_outbox(db_path, ids: list[int]) -> None:
    if not ids:
        return
    await _execute(
        db_path,
        "DELETE FROM outbox WHERE id IN (%s)" % ",".join("?" for _ in ids),
        tuple(ids),
    )

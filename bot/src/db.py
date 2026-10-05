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
CREATE TABLE IF NOT EXISTS local_credentials(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  player_key TEXT UNIQUE NOT NULL,
  display_name TEXT,
  is_admin INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_local_credentials_key
  ON local_credentials(player_key);
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
  tournament_id INTEGER REFERENCES tournaments(id) ON DELETE CASCADE,
  casual_tee_time_id TEXT REFERENCES casual_tee_times(id) ON DELETE CASCADE,
  round_number INTEGER NOT NULL DEFAULT 1 CHECK(round_number BETWEEN 1 AND 5),
  player_discord_id TEXT,
  team_id INTEGER REFERENCES teams(id) ON DELETE SET NULL,
  tee_time_id INTEGER REFERENCES tee_times(id) ON DELETE SET NULL,
  holes_json TEXT NOT NULL,
  total INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','verified','in_progress')),
  submitted_at TEXT NOT NULL,
  verified_by TEXT,
  submitted_by TEXT,
  witness_name TEXT,
  CHECK((tournament_id IS NULL) != (casual_tee_time_id IS NULL))
);
-- Shot-by-shot tracking (optional): where each shot landed on the hole,
-- as normalized 0..1 coordinates on the hole schematic. lie is where the
-- shot finished: tee, fairway, rough, sand, green. The final shot of a hole
-- carries holed=1. putts counts putts on a green shot instead of tapping
-- one point per putt (0 = not a putt-counted shot; the counted putts finish
-- the hole). Replaced wholesale per hole by set_hole_shots.
CREATE TABLE IF NOT EXISTS hole_shots(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  scorecard_id INTEGER NOT NULL REFERENCES scorecards(id) ON DELETE CASCADE,
  hole_number INTEGER NOT NULL CHECK(hole_number BETWEEN 1 AND 18),
  seq INTEGER NOT NULL CHECK(seq >= 1),
  x REAL NOT NULL CHECK(x >= 0 AND x <= 1),
  y REAL NOT NULL CHECK(y >= 0 AND y <= 1),
  lie TEXT NOT NULL CHECK(lie IN ('tee','fairway','rough','sand','green')),
  holed INTEGER NOT NULL DEFAULT 0 CHECK(holed IN (0,1)),
  putts INTEGER NOT NULL DEFAULT 0 CHECK(putts >= 0 AND putts <= 10),
  UNIQUE(scorecard_id, hole_number, seq)
);
CREATE INDEX IF NOT EXISTS idx_hole_shots_card ON hole_shots(scorecard_id);
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
  created_at TEXT NOT NULL,
  start_date TEXT,
  end_date TEXT
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

-- Push notifications (APNs). Devices register their push token via the
-- companion app; notification_prefs holds per-user toggles (all default
-- on). push_outbox is drained by a background loop that sends via APNs.
CREATE TABLE IF NOT EXISTS devices(
  discord_id TEXT NOT NULL,
  push_token TEXT NOT NULL,
  platform TEXT NOT NULL DEFAULT 'ios',
  updated_at TEXT NOT NULL,
  PRIMARY KEY (discord_id, push_token)
);
CREATE INDEX IF NOT EXISTS idx_devices_discord ON devices(discord_id);
CREATE TABLE IF NOT EXISTS notification_prefs(
  discord_id TEXT PRIMARY KEY,
  tournament_starts INTEGER NOT NULL DEFAULT 1,
  round_starts INTEGER NOT NULL DEFAULT 1,
  ace INTEGER NOT NULL DEFAULT 1,
  albatross INTEGER NOT NULL DEFAULT 1,
  top3_changes INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS push_outbox(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  discord_id TEXT NOT NULL,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  data_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  sent_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_push_outbox_unsent ON push_outbox(sent_at);
-- Dedup for one-shot notifications (tournament/round starts).
CREATE TABLE IF NOT EXISTS push_sent_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  ref_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(kind, ref_id)
);
-- Last-known top-3 (player discord_ids in order) per tournament, used to
-- detect top-3 leaderboard changes worth notifying about.
CREATE TABLE IF NOT EXISTS leaderboard_snapshots(
  tournament_id TEXT PRIMARY KEY,
  top3_json TEXT NOT NULL DEFAULT '[]',
  updated_at TEXT NOT NULL
);

-- Casual tee times: free-form rounds outside tournaments. No membership
-- limits — a player may join any number of them.
CREATE TABLE IF NOT EXISTS casual_tee_times(
  id TEXT PRIMARY KEY,
  creator_discord_id TEXT NOT NULL,
  label TEXT NOT NULL,
  course TEXT NOT NULL,
  pars TEXT NOT NULL,
  tee_position TEXT NOT NULL DEFAULT 'middle',
  pin_position TEXT NOT NULL DEFAULT 'white',
  wind_strength TEXT NOT NULL DEFAULT 'moderate',
  green_speed TEXT NOT NULL DEFAULT 'medium',
  starts_at TEXT NOT NULL,
  max_players INTEGER NOT NULL DEFAULT 4,
  notes TEXT NOT NULL DEFAULT '',
  format TEXT NOT NULL DEFAULT 'stroke'
    CHECK(format IN ('stroke','best_ball','match_play','alt_shot')),
  matchplay_tee_time_id TEXT,
  altshot_tee_time_id TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS casual_tee_time_players(
  tee_time_id TEXT NOT NULL,
  discord_id TEXT NOT NULL,
  joined_at TEXT NOT NULL,
  PRIMARY KEY (tee_time_id, discord_id)
);
CREATE INDEX IF NOT EXISTS idx_casual_players_discord ON casual_tee_time_players(discord_id);

-- Alt-shot records: team alternate-shot rounds. A tee time holds 1-2 teams.
-- A 1-team tee time has a fixed roster size (team_size 2-4, chosen at
-- creation): other players join the single team until it is full. A 2-team
-- tee time has two pre-created teams (team_number 1/2), each with the same
-- fixed roster size (team_size 2-4): the creator starts on team 1 and
-- other players pick a team to join. Only registered players may play on
-- 2-team tee times — no typed-in guest names.
-- Team rosters live in altshot_team_members: registered players (discord_id)
-- and, on 1-team / legacy 2-team tee times only, text names for partners
-- who aren't in the app. Scores are submitted once per team — not live.
-- Teams must have full rosters to submit (1-team: the single team; 2-team:
-- both teams). Record leaderboards are split by team size (2, 3, or 4
-- players). No record-change notifications, by design.
CREATE TABLE IF NOT EXISTS altshot_tee_times(
  id TEXT PRIMARY KEY,
  creator_discord_id TEXT NOT NULL,
  label TEXT NOT NULL,
  course TEXT NOT NULL,
  pars TEXT NOT NULL,
  tee_position TEXT NOT NULL DEFAULT 'middle',
  pin_position TEXT NOT NULL DEFAULT 'white',
  wind_strength TEXT NOT NULL DEFAULT 'moderate',
  green_speed TEXT NOT NULL DEFAULT 'medium',
  starts_at TEXT NOT NULL,
  max_teams INTEGER NOT NULL DEFAULT 2,
  team_size INTEGER,
  notes TEXT NOT NULL DEFAULT '',
  casual_tee_time_id TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS altshot_teams(
  id TEXT PRIMARY KEY,
  tee_time_id TEXT NOT NULL,
  team_number INTEGER NOT NULL DEFAULT 1,
  player1_discord_id TEXT,
  team_name TEXT NOT NULL DEFAULT '',
  player2_name TEXT NOT NULL DEFAULT '',
  player3_name TEXT NOT NULL DEFAULT '',
  player4_name TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_altshot_teams_tt ON altshot_teams(tee_time_id);
CREATE TABLE IF NOT EXISTS altshot_team_members(
  id TEXT PRIMARY KEY,
  team_id TEXT NOT NULL,
  discord_id TEXT,
  name TEXT NOT NULL DEFAULT '',
  position INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_altshot_members_team
  ON altshot_team_members(team_id);
CREATE TABLE IF NOT EXISTS altshot_scores(
  team_id TEXT PRIMARY KEY,
  holes TEXT NOT NULL,
  total INTEGER NOT NULL,
  submitted_by TEXT NOT NULL,
  submitted_at TEXT NOT NULL
);

-- Match-play records: head-to-head (single) or best-ball team matches.
-- Every tee time has exactly 2 sides. 'single' = 1 player per side;
-- 'bestball' = team_size (2-4) players per side, same size both sides,
-- with an optional team name per side. All participants must be registered
-- app users (Discord-linked) — win/loss records need identity, so there
-- are no text-name guests. Both sides must be full before any score can be
-- saved. Scores are live (hole-by-hole); the server auto-completes the
-- match when the result is decided (3&2, 2 UP, All Square, ...).
-- matchplay_records holds per-player W-L-T tallies keyed by format only
-- ('single' for 1v1, 'bestball' for team best-ball). Migrated from the old
-- course+setup-keyed shape in _migrate (table is dropped and recreated).
CREATE TABLE IF NOT EXISTS matchplay_tee_times(
  id TEXT PRIMARY KEY,
  creator_discord_id TEXT NOT NULL,
  label TEXT NOT NULL,
  course TEXT NOT NULL,
  pars TEXT NOT NULL,
  tee_position TEXT NOT NULL DEFAULT 'back',
  pin_position TEXT NOT NULL DEFAULT 'black',
  wind_strength TEXT NOT NULL DEFAULT 'moderate',
  green_speed TEXT NOT NULL DEFAULT 'pro',
  starts_at TEXT NOT NULL,
  format TEXT NOT NULL DEFAULT 'single',
  team_size INTEGER NOT NULL DEFAULT 1,
  notes TEXT NOT NULL DEFAULT '',
  casual_tee_time_id TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS matchplay_sides(
  id TEXT PRIMARY KEY,
  tee_time_id TEXT NOT NULL,
  side_number INTEGER NOT NULL,
  team_name TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  UNIQUE (tee_time_id, side_number)
);
CREATE INDEX IF NOT EXISTS idx_matchplay_sides_tt
  ON matchplay_sides(tee_time_id);
CREATE TABLE IF NOT EXISTS matchplay_side_members(
  id TEXT PRIMARY KEY,
  side_id TEXT NOT NULL,
  discord_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (side_id, discord_id)
);
CREATE INDEX IF NOT EXISTS idx_matchplay_side_members_side
  ON matchplay_side_members(side_id);
CREATE TABLE IF NOT EXISTS matchplay_scores(
  tee_time_id TEXT PRIMARY KEY,
  hole_results TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'in_progress',
  winner_side INTEGER,
  result_text TEXT NOT NULL DEFAULT '',
  submitted_by TEXT NOT NULL,
  submitted_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS matchplay_records(
  id TEXT PRIMARY KEY,
  format TEXT NOT NULL,
  discord_id TEXT NOT NULL,
  player_name TEXT NOT NULL DEFAULT '',
  wins INTEGER NOT NULL DEFAULT 0,
  losses INTEGER NOT NULL DEFAULT 0,
  ties INTEGER NOT NULL DEFAULT 0,
  UNIQUE (format, discord_id)
);

-- Global team-name lock (AltShot + Matchplay best-ball). A name, once
-- chosen, belongs to the crew that first used it: no other group of
-- players may play under it. team_name is the normalized key
-- (strip + collapse whitespace + casefold); owner_ids is a JSON array
-- of sorted discord_id strings. A row starts pending (is_final=0,
-- pending_claim set to the team/side that claimed it) and is finalized
-- to the full roster once the roster is full or a score is submitted.
CREATE TABLE IF NOT EXISTS team_name_registry(
  team_name TEXT PRIMARY KEY,
  display_name TEXT NOT NULL DEFAULT '',
  owner_ids TEXT NOT NULL DEFAULT '[]',
  is_final INTEGER NOT NULL DEFAULT 0,
  pending_claim TEXT,
  created_at TEXT NOT NULL DEFAULT ''
);
"""


def _json_list(raw) -> list:
    """Decode a JSON-encoded int list column; [] on anything unexpected."""
    try:
        val = json.loads(raw or "[]")
    except (ValueError, TypeError):
        return []
    return [int(x) for x in val] if isinstance(val, list) else []


def utcnow_iso() -> str:    return datetime.now(timezone.utc).isoformat()


# Window for "upcoming" tee-time lists: a tee time whose start is this many
# minutes in the past still shows as upcoming. A four-man round takes ~2h,
# and the list entry is the only path to score entry, so tee times must not
# drop off mid-round. (The original 10-minute grace was for backdating a tee
# time at setup — creation never rejected past starts, so that still works.)
# Deliberately NOT applied to get_reminder_due (push reminders must only
# target future starts).
_UPCOMING_GRACE_MINUTES = 180


def upcoming_bound_iso() -> str:
    """ISO-8601 lower bound for upcoming tee-time filters (now minus grace).

    Same string format as utcnow_iso(), so lexicographic comparison stays
    chronological (see get_reminder_due).
    """
    return (datetime.now(timezone.utc)
            - timedelta(minutes=_UPCOMING_GRACE_MINUTES)).isoformat()


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
    - Rebuilds the scorecards table when its status CHECK predates
      'in_progress' (live hole-by-hole entry), same copy-preserve pattern.
    - Rebuilds the scorecards table when casual_tee_time_id is missing:
      tournament_id becomes nullable and exactly one of tournament_id /
      casual_tee_time_id must be set (CHECK).
    - Adds casual_tee_times.format / matchplay_tee_time_id /
      altshot_tee_time_id when missing (casual formats).
    - Adds matchplay_tee_times.casual_tee_time_id and
      altshot_tee_times.casual_tee_time_id when missing (back-references so
      linked games stay out of the dedicated tabs).
    - Adds players.golfplus_handle / timezone / stats_private when missing.
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
        if "stats_private" not in player_cols:
            await con.execute(
                "ALTER TABLE players ADD COLUMN stats_private INTEGER"
                " NOT NULL DEFAULT 0"
            )
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

        # hole_shots.putts — putt count on a green shot instead of one
        # point per putt (0 = not putt-counted; older rows default to 0).
        cur = await con.execute("PRAGMA table_info(hole_shots)")
        hs_cols = [r[1] for r in await cur.fetchall()]
        if "putts" not in hs_cols:
            await con.execute(
                "ALTER TABLE hole_shots ADD COLUMN putts INTEGER"
                " NOT NULL DEFAULT 0 CHECK(putts >= 0 AND putts <= 10)"
            )
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

        # seasons.start_date / end_date (nullable; older seasons have none).
        cur = await con.execute("PRAGMA table_info(seasons)")
        season_cols = [r[1] for r in await cur.fetchall()]
        for col in ("start_date", "end_date"):
            if col not in season_cols:
                await con.execute(
                    f"ALTER TABLE seasons ADD COLUMN {col} TEXT")
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

        # scorecards.status gains 'in_progress' (live hole-by-hole entry).
        # Rebuild with the new CHECK, preserving every row.
        cur = await con.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'scorecards'"
        )
        row = await cur.fetchone()
        card_sql = row[0] if row else ""
        if "'in_progress'" not in card_sql:
            await con.execute(
                """CREATE TABLE scorecards_new(
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  tournament_id INTEGER NOT NULL REFERENCES tournaments(id) ON DELETE CASCADE,
                  round_number INTEGER NOT NULL DEFAULT 1 CHECK(round_number BETWEEN 1 AND 5),
                  player_discord_id TEXT,
                  team_id INTEGER REFERENCES teams(id) ON DELETE SET NULL,
                  tee_time_id INTEGER REFERENCES tee_times(id) ON DELETE SET NULL,
                  holes_json TEXT NOT NULL,
                  total INTEGER NOT NULL,
                  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','verified','in_progress')),
                  submitted_at TEXT NOT NULL,
                  verified_by TEXT,
                  submitted_by TEXT,
                  witness_name TEXT
                )"""
            )
            await con.execute(
                """INSERT INTO scorecards_new
                  (id, tournament_id, round_number, player_discord_id, team_id,
                   tee_time_id, holes_json, total, status, submitted_at,
                   verified_by, submitted_by, witness_name)
                SELECT id, tournament_id, round_number, player_discord_id, team_id,
                   tee_time_id, holes_json, total, status, submitted_at,
                   verified_by, submitted_by, witness_name
                FROM scorecards"""
            )
            await con.execute("DROP TABLE scorecards")
            await con.execute("ALTER TABLE scorecards_new RENAME TO scorecards")
            await con.commit()

        # Casual formats: scorecards gain casual_tee_time_id and tournament_id
        # becomes nullable (exactly one of the two is set — enforced by the
        # new CHECK). Rebuild with the new shape, preserving every row.
        cur = await con.execute("PRAGMA table_info(scorecards)")
        card_cols2 = [r[1] for r in await cur.fetchall()]
        if "casual_tee_time_id" not in card_cols2:
            await con.execute(
                """CREATE TABLE scorecards_new(
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  tournament_id INTEGER REFERENCES tournaments(id)
                    ON DELETE CASCADE,
                  casual_tee_time_id TEXT REFERENCES casual_tee_times(id)
                    ON DELETE CASCADE,
                  round_number INTEGER NOT NULL DEFAULT 1
                    CHECK(round_number BETWEEN 1 AND 5),
                  player_discord_id TEXT,
                  team_id INTEGER REFERENCES teams(id) ON DELETE SET NULL,
                  tee_time_id INTEGER REFERENCES tee_times(id)
                    ON DELETE SET NULL,
                  holes_json TEXT NOT NULL,
                  total INTEGER NOT NULL,
                  status TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending','verified','in_progress')),
                  submitted_at TEXT NOT NULL,
                  verified_by TEXT,
                  submitted_by TEXT,
                  witness_name TEXT,
                  CHECK((tournament_id IS NULL)
                        != (casual_tee_time_id IS NULL))
                )"""
            )
            await con.execute(
                """INSERT INTO scorecards_new
                  (id, tournament_id, casual_tee_time_id, round_number,
                   player_discord_id, team_id, tee_time_id, holes_json, total,
                   status, submitted_at, verified_by, submitted_by,
                   witness_name)
                SELECT id, tournament_id, NULL, round_number,
                   player_discord_id, team_id, tee_time_id, holes_json, total,
                   status, submitted_at, verified_by, submitted_by,
                   witness_name
                FROM scorecards"""
            )
            await con.execute("DROP TABLE scorecards")
            await con.execute("ALTER TABLE scorecards_new RENAME TO scorecards")
            await con.commit()

        # Casual scorecards index: create (or ensure) once the column exists,
        # whether via the fresh schema or the rebuild above.
        await con.execute(
            "CREATE INDEX IF NOT EXISTS idx_scorecards_casual"
            " ON scorecards(casual_tee_time_id)")
        await con.commit()

        # Casual formats on casual_tee_times: format + links to the existing
        # match-play / alt-shot engines.
        cur = await con.execute("PRAGMA table_info(casual_tee_times)")
        ctt_cols = [r[1] for r in await cur.fetchall()]
        if "format" not in ctt_cols:
            await con.execute(
                "ALTER TABLE casual_tee_times ADD COLUMN format TEXT"
                " NOT NULL DEFAULT 'stroke'")
            await con.commit()
        if "matchplay_tee_time_id" not in ctt_cols:
            await con.execute(
                "ALTER TABLE casual_tee_times"
                " ADD COLUMN matchplay_tee_time_id TEXT")
            await con.commit()
        if "altshot_tee_time_id" not in ctt_cols:
            await con.execute(
                "ALTER TABLE casual_tee_times ADD COLUMN altshot_tee_time_id TEXT")
            await con.commit()

        # Back-references so linked games stay out of the dedicated tabs'
        # lists (they live in the Casual tab instead).
        cur = await con.execute("PRAGMA table_info(matchplay_tee_times)")
        mp_cols = [r[1] for r in await cur.fetchall()]
        if "casual_tee_time_id" not in mp_cols:
            await con.execute(
                "ALTER TABLE matchplay_tee_times"
                " ADD COLUMN casual_tee_time_id TEXT")
            await con.commit()
        cur = await con.execute("PRAGMA table_info(altshot_tee_times)")
        as_cols = [r[1] for r in await cur.fetchall()]
        if "casual_tee_time_id" not in as_cols:
            await con.execute(
                "ALTER TABLE altshot_tee_times"
                " ADD COLUMN casual_tee_time_id TEXT")
            await con.commit()


        # Alt-shot fixed rosters: altshot_tee_times.team_size (NULL = flexible
        # 2-team tee times; 2/3/4 = fixed 1-team roster) and the
        # altshot_team_members roster table. Backfill roster rows for teams
        # created before the roster table existed (owner + text names).
        cur = await con.execute("PRAGMA table_info(altshot_tee_times)")
        alt_cols = [r[1] for r in await cur.fetchall()]
        if "team_size" not in alt_cols:
            await con.execute(
                "ALTER TABLE altshot_tee_times ADD COLUMN team_size INTEGER")
            await con.commit()
        await con.execute(
            """CREATE TABLE IF NOT EXISTS altshot_team_members(
              id TEXT PRIMARY KEY,
              team_id TEXT NOT NULL,
              discord_id TEXT,
              name TEXT NOT NULL DEFAULT '',
              position INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL)""")
        await con.execute(
            "CREATE INDEX IF NOT EXISTS idx_altshot_members_team"
            " ON altshot_team_members(team_id)")
        await con.commit()
        cur = await con.execute(
            "SELECT t.id, t.player1_discord_id, t.player2_name,"
            " t.player3_name, t.player4_name,"
            " COALESCE(p.display_name,"
            " '<@' || t.player1_discord_id || '>') AS pname"
            " FROM altshot_teams t LEFT JOIN players p"
            " ON p.discord_id = t.player1_discord_id"
            " WHERE NOT EXISTS (SELECT 1 FROM altshot_team_members m"
            " WHERE m.team_id = t.id)")
        import uuid as _uuid
        from datetime import datetime as _dt, timezone as _tz
        _now = _dt.now(_tz.utc).isoformat()
        for tid, did, p2, p3, p4, pname in await cur.fetchall():
            await con.execute(
                "INSERT INTO altshot_team_members (id, team_id, discord_id,"
                " name, position, created_at) VALUES (?,?,?,?,?,?)",
                (_uuid.uuid4().hex[:12], tid, did, pname, 1, _now))
            _pos = 2
            for _extra in (p2, p3, p4):
                if (_extra or "").strip():
                    await con.execute(
                        "INSERT INTO altshot_team_members (id, team_id,"
                        " discord_id, name, position, created_at)"
                        " VALUES (?,?,?,?,?,?)",
                        (_uuid.uuid4().hex[:12], tid, None,
                         _extra.strip(), _pos, _now))
                    _pos += 1
        await con.commit()

        # Alt-shot 2-team rework: altshot_teams gains an explicit team_number
        # (1/2, creation order within the tee time) and player1_discord_id
        # becomes nullable (the pre-created second team of a 2-team tee
        # time has no founder yet); the old
        # UNIQUE(tee_time_id, player1_discord_id) is dropped. Idempotent:
        # skipped once team_number exists.
        cur = await con.execute("PRAGMA table_info(altshot_teams)")
        _team_cols = [r[1] for r in await cur.fetchall()]
        if "team_number" not in _team_cols:
            await con.execute(
                """CREATE TABLE altshot_teams_new(
                  id TEXT PRIMARY KEY,
                  tee_time_id TEXT NOT NULL,
                  team_number INTEGER NOT NULL DEFAULT 1,
                  player1_discord_id TEXT,
                  team_name TEXT NOT NULL DEFAULT '',
                  player2_name TEXT NOT NULL DEFAULT '',
                  player3_name TEXT NOT NULL DEFAULT '',
                  player4_name TEXT NOT NULL DEFAULT '',
                  created_at TEXT NOT NULL)""")
            cur2 = await con.execute(
                "SELECT id, tee_time_id, player1_discord_id, team_name,"
                " player2_name, player3_name, player4_name, created_at"
                " FROM altshot_teams ORDER BY tee_time_id, created_at, id")
            _tnum = {}
            for (_tid, _ttid, _p1, _tname, _p2, _p3, _p4,
                    _created) in await cur2.fetchall():
                _tnum[_ttid] = _tnum.get(_ttid, 0) + 1
                await con.execute(
                    "INSERT INTO altshot_teams_new (id, tee_time_id,"
                    " team_number, player1_discord_id, team_name,"
                    " player2_name, player3_name, player4_name, created_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?)",
                    (_tid, _ttid, _tnum[_ttid], _p1, _tname, _p2, _p3,
                     _p4, _created))
            await con.execute("DROP TABLE altshot_teams")
            await con.execute(
                "ALTER TABLE altshot_teams_new RENAME TO altshot_teams")
            await con.execute(
                "CREATE INDEX IF NOT EXISTS idx_altshot_teams_tt"
                " ON altshot_teams(tee_time_id)")
            await con.commit()

        # matchplay_records v2: tallies are keyed by format ('single' /
        # 'bestball') only. If the old course+setup-keyed table exists,
        # drop it and recreate the new shape. Not yet deployed to
        # production, so no rows need preserving. Idempotent: a fresh
        # SCHEMA already has the new shape (no 'course' column -> skip).
        cur = await con.execute("PRAGMA table_info(matchplay_records)")
        _rec_cols = [r[1] for r in await cur.fetchall()]
        if _rec_cols and "course" in _rec_cols:
            await con.execute("DROP TABLE matchplay_records")
            await con.commit()
            await con.execute(
                """CREATE TABLE matchplay_records(
                  id TEXT PRIMARY KEY,
                  format TEXT NOT NULL,
                  discord_id TEXT NOT NULL,
                  player_name TEXT NOT NULL DEFAULT '',
                  wins INTEGER NOT NULL DEFAULT 0,
                  losses INTEGER NOT NULL DEFAULT 0,
                  ties INTEGER NOT NULL DEFAULT 0,
                  UNIQUE (format, discord_id))""")
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


async def list_players(db_path) -> list[dict]:
    """All registered players, for the admin player list. Ordered by name."""
    return await _fetchall(
        db_path,
        "SELECT discord_id, display_name, golfplus_handle FROM players"
        " ORDER BY display_name ASC",
    )


async def delete_player_data(db_path, discord_id: str) -> dict:
    """Permanently erase one player's personal data (account deletion).

    Deletes every row where the user is the subject:
      players, local_credentials, devices, notification_prefs, push_outbox,
      registrations, team_members, tee_time_players, join_requests,
      scorecards, season_points, casual_tee_time_players,
      altshot_team_members, matchplay_side_members, matchplay_records,
      hole_shots (via the deleted scorecards).
    altshot_teams.player1_discord_id is SET NULL (the column is nullable and
    the team row is shared with the other teammates).

    Deliberately LEFT intact (documented here so nobody "fixes" it later):
      tournaments, tee_times, teams, casual/altshot/matchplay tee times and
      their scores, seasons, season_tournaments, matches (shared tournament
      history that also belongs to the opponent), side_quests, boards,
      outbox, tournament_leaders, leaderboard_snapshots, push_sent_log,
      team_name_registry, and all created_by / submitted_by / verified_by /
      reported_by / decided_by / logged_by audit columns. Some of those
      audit columns are NOT NULL; they identify shared community entities,
      not the deleted user as personal data.
    """
    tables = [
        ("players", "discord_id"),
        ("local_credentials", "player_key"),
        ("devices", "discord_id"),
        ("notification_prefs", "discord_id"),
        ("push_outbox", "discord_id"),
        ("registrations", "player_discord_id"),
        ("team_members", "player_discord_id"),
        ("tee_time_players", "player_discord_id"),
        ("join_requests", "player_discord_id"),
        ("scorecards", "player_discord_id"),
        ("season_points", "player_discord_id"),
        ("casual_tee_time_players", "discord_id"),
        ("altshot_team_members", "discord_id"),
        ("matchplay_side_members", "discord_id"),
        ("matchplay_records", "discord_id"),
    ]
    deleted: dict[str, int] = {}
    # hole_shots belong to the player's scorecards (no player key of its
    # own) — remove them BEFORE the scorecards rows disappear below.
    _, n = await _execute(
        db_path,
        "DELETE FROM hole_shots WHERE scorecard_id IN"
        " (SELECT id FROM scorecards WHERE player_discord_id = ?)",
        (discord_id,),
    )
    deleted["hole_shots"] = n
    for table, col in tables:
        _, n = await _execute(
            db_path, f"DELETE FROM {table} WHERE {col} = ?", (discord_id,))
        deleted[table] = n
    _, n = await _execute(
        db_path,
        "UPDATE altshot_teams SET player1_discord_id = NULL"
        " WHERE player1_discord_id = ?",
        (discord_id,),
    )
    deleted["altshot_teams.player1_discord_id_nulled"] = n
    return {"discord_id": discord_id, "deleted": deleted}


# ------------------------------------------------------- local credentials
# Email+password accounts (alternative to Discord OAuth). Each account owns a
# synthetic identity "local:<32 hex>" stored as players.discord_id so every
# scorecard / season-points / crew-list query works unchanged.


async def create_local_user(db_path, email: str, password_hash: str,
                            player_key: str, display_name: str) -> None:
    """Insert local_credentials + matching players row, atomically.

    Email pre-lowercased. Raises on duplicate email or player_key."""
    async with aiosqlite.connect(db_path) as con:
        await con.execute(
            "INSERT INTO local_credentials (email, password_hash, player_key,"
            " display_name, created_at) VALUES (?,?,?,?,?)",
            (email, password_hash, player_key, display_name, utcnow_iso()),
        )
        await con.execute(
            "INSERT INTO players (discord_id, display_name) VALUES (?, ?)",
            (player_key, display_name),
        )
        await con.commit()


async def get_local_credential_by_email(db_path, email: str) -> dict | None:
    return await _fetchone(
        db_path, "SELECT * FROM local_credentials WHERE email = ?",
        (email.strip().lower(),),
    )


async def get_local_credential_by_key(db_path, player_key: str) -> dict | None:
    return await _fetchone(
        db_path, "SELECT * FROM local_credentials WHERE player_key = ?",
        (player_key,),
    )


async def list_local_credentials(db_path) -> list[dict]:
    """player_key, email, display_name, is_admin for all local accounts."""
    return await _fetchall(
        db_path,
        "SELECT player_key, email, display_name, is_admin"
        " FROM local_credentials",
    )


async def set_local_admin(db_path, player_key: str, is_admin: bool) -> bool:
    """Grant/revoke the local admin flag. Returns False when unknown key."""
    _, n = await _execute(
        db_path,
        "UPDATE local_credentials SET is_admin = ? WHERE player_key = ?",
        (1 if is_admin else 0, player_key),
    )
    return n == 1


async def set_local_password_hash(db_path, player_key: str,
                                  password_hash: str) -> bool:
    """Replace the stored bcrypt hash (password reset)."""
    _, n = await _execute(
        db_path,
        "UPDATE local_credentials SET password_hash = ? WHERE player_key = ?",
        (password_hash, player_key),
    )
    return n == 1


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
    """Returns 'ok', 'full', 'already', 'round_conflict', or 'missing'.

    'round_conflict': the player is already in a different tee time for the
    same tournament round. One tee time per player per round — no exceptions.
    """
    tt = await get_tee_time(db_path, tee_time_id)
    if tt is None:
        return "missing"
    players = await get_tee_time_players(db_path, tee_time_id)
    if any(p["discord_id"] == discord_id for p in players):
        return "already"
    conflict = await tee_time_for_round(
        db_path, tt["tournament_id"], tt.get("round_number") or 1, discord_id,
        exclude_tee_time_id=tee_time_id,
    )
    if conflict is not None:
        return "round_conflict"
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


async def is_player_in_tee_time(db_path, tee_time_id, discord_id) -> bool:
    """Direct membership check — is this player in THIS tee time?

    Prefer this over get_player_tee_time() when gating an action on a specific
    tee time: get_player_tee_time() returns one arbitrary tee time per
    tournament, which misfires when a player is in several.
    """
    row = await _fetchone(
        db_path,
        "SELECT 1 FROM tee_time_players"
        " WHERE tee_time_id = ? AND player_discord_id = ?",
        (tee_time_id, discord_id),
    )
    return row is not None


async def tee_time_for_round(db_path, tournament_id, round_number,
                             discord_id,
                             exclude_tee_time_id=None) -> dict | None:
    """Another tee time in this tournament + round the player is in.

    One tee time per player per round — a submitted scorecard does NOT free
    the player to join a second one, because nobody may play a round twice.
    """
    rows = await _fetchall(
        db_path,
        "SELECT tt.* FROM tee_times tt"
        " JOIN tee_time_players tp ON tp.tee_time_id = tt.id"
        " WHERE tt.tournament_id = ? AND tt.round_number = ?"
        " AND tp.player_discord_id = ?",
        (tournament_id, round_number, discord_id),
    )
    for tt in rows:
        if exclude_tee_time_id is not None and tt["id"] == exclude_tee_time_id:
            continue
        return tt
    return None


async def find_round_card(db_path, tournament_id, discord_id,
                          round_number) -> dict | None:
    """The scorecard covering this player for this tournament + round, in any
    tee time — their own card, or a team card for a team they're on.

    Enforces one scorecard per round per member: a second card for the same
    round (even from a different tee time) is refused by the submit paths.
    """
    round_number = max(1, min(5, int(round_number or 1)))
    return await _fetchone(
        db_path,
        "SELECT s.*, tt.label AS tee_time_label"
        " FROM scorecards s JOIN tee_times tt ON tt.id = s.tee_time_id"
        " WHERE s.tournament_id = ? AND s.round_number = ?"
        " AND (s.player_discord_id = ?"
        "      OR (s.team_id IS NOT NULL AND s.player_discord_id IS NULL"
        "          AND EXISTS ("
        "            SELECT 1 FROM team_members tm"
        "            WHERE tm.team_id = s.team_id"
        "              AND tm.player_discord_id = ?)))"
        " ORDER BY s.submitted_at DESC LIMIT 1",
        (tournament_id, round_number, discord_id, discord_id),
    )


async def leave_all_tee_times(db_path, tournament_id, discord_id) -> int:
    """Remove a player from every tee time they're in for this tournament.
    Returns the number of tee times left."""
    rows = await _fetchall(
        db_path,
        "SELECT tt.id FROM tee_times tt"
        " JOIN tee_time_players tp ON tp.tee_time_id = tt.id"
        " WHERE tt.tournament_id = ? AND tp.player_discord_id = ?",
        (tournament_id, discord_id),
    )
    for r in rows:
        await leave_tee_time(db_path, r["id"], discord_id)
    return len(rows)


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
async def create_season(db_path, guild_id, name, created_by,
                      start_date: str | None = None,
                      end_date: str | None = None) -> int:
    lastrowid, _ = await _execute(
        db_path,
        "INSERT INTO seasons (guild_id, name, status, created_by, created_at,"
        " start_date, end_date)"
        " VALUES (?,?,'active',?,?,?,?)",
        (guild_id, name, created_by, utcnow_iso(), start_date, end_date),
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


async def get_active_seasons(db_path, guild_id) -> list[dict]:
    """All active seasons (oldest first) for auto-linking new tournaments."""
    return await _fetchall(
        db_path,
        "SELECT * FROM seasons WHERE guild_id = ? AND status = 'active'"
        " ORDER BY id ASC",
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
                           scores: list[int | None], status: str,
                           submitted_by: str | None = None,
                           round_number: int = 1,
                           witness_name: str | None = None,
                           casual_tee_time_id: str | None = None) -> int:
    """Insert or update a scorecard. scores may contain None for holes not
    yet played (live entry); total covers entered holes only.

    Card identity: best_ball matches per member (player + team); shared team
    formats (alt_shot/scramble) match per team; stroke matches per player.
    Casual cards pass casual_tee_time_id (tournament_id must be None then);
    casual cards are always per-player (team_id None, round 1).
    """
    holes_json = json.dumps(scores)
    total = sum(s for s in scores if s is not None)
    now = utcnow_iso()
    round_number = max(1, min(5, int(round_number or 1)))
    witness_name = (witness_name or "").strip()[:80] or None
    if casual_tee_time_id is not None:
        scope_where = "tournament_id IS NULL AND casual_tee_time_id = ?"
        scope_params: tuple = (casual_tee_time_id,)
    else:
        scope_where = "tournament_id = ? AND casual_tee_time_id IS NULL"
        scope_params = (tournament_id,)
    if team_id is not None and player_id is not None:
        # best_ball: each member has their own card.
        existing = await _fetchone(
            db_path,
            "SELECT id FROM scorecards WHERE " + scope_where +
            " AND player_discord_id = ? AND team_id = ?"
            " AND IFNULL(tee_time_id, -1) = IFNULL(?, -1)"
            " AND round_number = ?"
            " ORDER BY submitted_at DESC LIMIT 1",
            scope_params + (player_id, team_id, tee_time_id, round_number),
        )
    elif team_id is not None:
        existing = await _fetchone(
            db_path,
            "SELECT id FROM scorecards WHERE " + scope_where +
            " AND team_id = ?"
            " AND IFNULL(tee_time_id, -1) = IFNULL(?, -1)"
            " AND round_number = ?"
            " ORDER BY submitted_at DESC LIMIT 1",
            scope_params + (team_id, tee_time_id, round_number),
        )
    else:
        existing = await _fetchone(
            db_path,
            "SELECT id FROM scorecards WHERE " + scope_where +
            " AND player_discord_id = ?"
            " AND IFNULL(tee_time_id, -1) = IFNULL(?, -1)"
            " AND team_id IS NULL AND round_number = ?"
            " ORDER BY submitted_at DESC LIMIT 1",
            scope_params + (player_id, tee_time_id, round_number),
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
        "INSERT INTO scorecards (tournament_id, casual_tee_time_id,"
        " round_number, player_discord_id,"
        " team_id, tee_time_id, holes_json, total, status, submitted_at,"
        " submitted_by, witness_name)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (tournament_id, casual_tee_time_id, round_number, player_id, team_id,
         tee_time_id, holes_json, total, status, now, submitted_by,
         witness_name),
    )
    return lastrowid


async def save_partial_scorecard(db_path, tournament_id, player_id, team_id,
                                 tee_time_id, scores: list[int | None],
                                 submitted_by: str | None = None,
                                 round_number: int = 1) -> int:
    """Live hole-by-hole save. scores is full-length with None for holes not
    yet played; non-null holes merge over the existing card so partners can
    enter on the same team card (or a player on their own) concurrently.

    The card is (re)created with status 'in_progress' — unless the existing
    card is already complete (pending/verified), in which case its status
    is preserved (crew correcting a submitted card). A no-op merge when
    nothing changed keeps the same row and returns its id.
    """
    round_number = max(1, min(5, int(round_number or 1)))
    holes_count = len(scores)
    now = utcnow_iso()
    # Single-connection transaction: two partners tapping the same shared
    # team card can't interleave a read-modify-write and lose a hole.
    con = await aiosqlite.connect(db_path)
    try:
        con.row_factory = aiosqlite.Row
        await con.execute("BEGIN IMMEDIATE")
        if team_id is not None and player_id is not None:
            # best_ball: one card per member.
            q = ("SELECT * FROM scorecards WHERE tournament_id = ?"
                 " AND player_discord_id = ? AND team_id = ?"
                 " AND IFNULL(tee_time_id, -1) = IFNULL(?, -1)"
                 " AND round_number = ? ORDER BY submitted_at DESC LIMIT 1")
            params = (tournament_id, player_id, team_id, tee_time_id,
                      round_number)
        elif team_id is not None:
            q = ("SELECT * FROM scorecards WHERE tournament_id = ?"
                 " AND team_id = ?"
                 " AND IFNULL(tee_time_id, -1) = IFNULL(?, -1)"
                 " AND round_number = ? ORDER BY submitted_at DESC LIMIT 1")
            params = (tournament_id, team_id, tee_time_id, round_number)
        else:
            q = ("SELECT * FROM scorecards WHERE tournament_id = ?"
                 " AND player_discord_id = ?"
                 " AND IFNULL(tee_time_id, -1) = IFNULL(?, -1)"
                 " AND team_id IS NULL AND round_number = ?"
                 " ORDER BY submitted_at DESC LIMIT 1")
            params = (tournament_id, player_id, tee_time_id, round_number)
        cur = await con.execute(q, params)
        existing = await cur.fetchone()
        if existing:
            existing = dict(existing)
            cur_scores = json.loads(existing["holes_json"])
            if len(cur_scores) != holes_count:
                raise ValueError(
                    f"Partial card has {len(cur_scores)} holes,"
                    f" expected {holes_count}."
                )
            merged = [
                new if new is not None else old
                for old, new in zip(cur_scores, scores)
            ]
            keep_status = (existing["status"]
                           if existing["status"] != "in_progress"
                           else "in_progress")
            if merged == cur_scores and existing["status"] == keep_status:
                await con.execute("ROLLBACK")
                return existing["id"]
            total = sum(s for s in merged if s is not None)
            await con.execute(
                "UPDATE scorecards SET holes_json = ?, total = ?, status = ?,"
                " submitted_at = ?, submitted_by = ? WHERE id = ?",
                (json.dumps(merged), total, keep_status, now,
                 submitted_by or existing.get("submitted_by"),
                 existing["id"]),
            )
            await con.commit()
            return existing["id"]
        total = sum(s for s in scores if s is not None)
        cur = await con.execute(
            "INSERT INTO scorecards (tournament_id, round_number,"
            " player_discord_id, team_id, tee_time_id, holes_json, total,"
            " status, submitted_at, submitted_by)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (tournament_id, round_number, player_id, team_id, tee_time_id,
             json.dumps(scores), total, "in_progress", now, submitted_by),
        )
        await con.commit()
        return cur.lastrowid
    except Exception:
        try:
            await con.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        await con.close()


async def find_scorecard(db_path, tournament_id, *, player_discord_id=None,
                         team_id=None, tee_time_id=None,
                         round_number=1) -> dict | None:
    """Return the card an upsert_scorecard call would overwrite, or None.

    Used to enforce the crew-only edit rule: a submission with no matching
    card is a first submission (open to tee-time members); a matching card
    means an edit, which only crew may perform.
    """
    round_number = max(1, min(5, int(round_number or 1)))
    if team_id is not None and player_discord_id is not None:
        # best_ball: one card per member.
        return await _fetchone(
            db_path,
            "SELECT * FROM scorecards WHERE tournament_id = ?"
            " AND player_discord_id = ? AND team_id = ?"
            " AND IFNULL(tee_time_id, -1) = IFNULL(?, -1)"
            " AND round_number = ? ORDER BY submitted_at DESC LIMIT 1",
            (tournament_id, player_discord_id, team_id, tee_time_id,
             round_number),
        )
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


# ------------------------------------------------------------ hole shots
LIES = ("tee", "fairway", "rough", "sand", "green")


async def set_hole_shots(db_path, scorecard_id: int, hole_number: int,
                         shots: list[dict]) -> int:
    """Replace one hole's tracked shots wholesale.

    Each shot: {x, y, lie, holed, putts}. seq is implied by list order (1-based).
    Returns the number of shots stored.
    """
    async with aiosqlite.connect(db_path) as con:
        await con.execute(
            "DELETE FROM hole_shots WHERE scorecard_id = ? AND hole_number = ?",
            (scorecard_id, hole_number),
        )
        for i, s in enumerate(shots, start=1):
            await con.execute(
                "INSERT INTO hole_shots"
                " (scorecard_id, hole_number, seq, x, y, lie, holed, putts)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (scorecard_id, hole_number, i,
                 float(s["x"]), float(s["y"]), s["lie"],
                 1 if s.get("holed") else 0,
                 int(s.get("putts") or 0)),
            )
        await con.commit()
    return len(shots)


async def get_hole_shots(db_path, scorecard_id: int,
                         hole_number: int) -> list[dict]:
    return await _fetchall(
        db_path,
        "SELECT seq, x, y, lie, holed, putts FROM hole_shots"
        " WHERE scorecard_id = ? AND hole_number = ? ORDER BY seq ASC",
        (scorecard_id, hole_number),
    )


async def get_shots_for_scorecards(db_path,
                                  card_ids: list[int]) -> dict[int, dict]:
    """{scorecard_id: {hole_number: [shot, ...]}} for the given cards."""
    if not card_ids:
        return {}
    placeholders = ",".join("?" for _ in card_ids)
    rows = await _fetchall(
        db_path,
        "SELECT scorecard_id, hole_number, seq, x, y, lie, holed, putts"
        f" FROM hole_shots WHERE scorecard_id IN ({placeholders})"
        " ORDER BY scorecard_id, hole_number, seq",
        tuple(card_ids),
    )
    out: dict[int, dict] = {}
    for r in rows:
        out.setdefault(r["scorecard_id"], {}).setdefault(
            r["hole_number"], []).append(r)
    return out


async def set_stats_private(db_path, discord_id: str, value: bool) -> None:
    await _execute(db_path,
                   "UPDATE players SET stats_private = ? WHERE discord_id = ?",
                   (1 if value else 0, discord_id))


async def get_scorecards(db_path, tournament_id, status=None) -> list[dict]:
    sql = "SELECT * FROM scorecards WHERE tournament_id = ?"
    params: list = [tournament_id]
    if status:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY submitted_at ASC"
    return await _fetchall(db_path, sql, tuple(params))


async def get_casual_scorecards(db_path, casual_tee_time_id,
                               status=None) -> list[dict]:
    """Scorecards for one casual tee time (stroke / best-ball formats)."""
    sql = ("SELECT * FROM scorecards WHERE tournament_id IS NULL"
           " AND casual_tee_time_id = ?")
    params: list = [casual_tee_time_id]
    if status:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY submitted_at ASC"
    return await _fetchall(db_path, sql, tuple(params))


async def find_casual_scorecard(db_path, casual_tee_time_id,
                               player_discord_id) -> dict | None:
    """Latest card for a player on a casual tee time (any status)."""
    return await _fetchone(
        db_path,
        "SELECT * FROM scorecards WHERE tournament_id IS NULL"
        " AND casual_tee_time_id = ? AND player_discord_id = ?"
        " AND team_id IS NULL ORDER BY submitted_at DESC LIMIT 1",
        (casual_tee_time_id, player_discord_id),
    )


async def get_latest_player_card(db_path, tournament_id, discord_id,
                                 round_number: int | None = None) -> dict | None:
    """Latest card for a player in a tournament (optionally one round).

    Matches the player's own cards in any format — including best_ball,
    whose cards carry both player_discord_id and team_id.
    """
    sql = ("SELECT * FROM scorecards WHERE tournament_id = ?"
           " AND player_discord_id = ?")
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
        (json.dumps(scores), sum(s for s in scores if s is not None), card_id),
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
    """Verified individual scorecards for a player across a guild's tournaments
    and casual rounds.

    Returns [{"holes_json", "pars", "holes"}]; team cards are excluded so
    personal stats stay personal.
    """
    return await _fetchall(
        db_path,
        "SELECT s.holes_json, t.pars, t.holes, s.submitted_at FROM scorecards s"
        " JOIN tournaments t ON t.id = s.tournament_id"
        " WHERE t.guild_id = ? AND s.player_discord_id = ?"
        " AND s.team_id IS NULL AND s.status = 'verified'"
        " UNION ALL"
        " SELECT s.holes_json, c.pars, 18, s.submitted_at FROM scorecards s"
        " JOIN casual_tee_times c ON c.id = s.casual_tee_time_id"
        " WHERE s.player_discord_id = ?"
        " AND s.team_id IS NULL AND s.status = 'verified'"
        " ORDER BY submitted_at ASC",
        (guild_id, player_discord_id, player_discord_id),
    )


async def get_player_stat_cards(db_path, guild_id,
                               player_discord_id) -> list[dict]:
    """Verified individual scorecards for shot stats + handicap.

    Same universe as get_player_verified_cards, but also returns the card
    id, total, and submitted_at so shot rows can be joined and handicap
    differentials ordered. Team cards excluded; only 'verified' (completed)
    cards. Casual stroke/best-ball cards are included (their pars come from
    the casual tee time).
    """
    return await _fetchall(
        db_path,
        "SELECT s.id, s.holes_json, s.total, s.submitted_at, t.pars, t.holes"
        " FROM scorecards s JOIN tournaments t ON t.id = s.tournament_id"
        " WHERE t.guild_id = ? AND s.player_discord_id = ?"
        " AND s.team_id IS NULL AND s.status = 'verified'"
        " UNION ALL"
        " SELECT s.id, s.holes_json, s.total, s.submitted_at, c.pars, 18"
        " FROM scorecards s JOIN casual_tee_times c"
        " ON c.id = s.casual_tee_time_id"
        " WHERE s.player_discord_id = ?"
        " AND s.team_id IS NULL AND s.status = 'verified'"
        " ORDER BY submitted_at ASC",
        (guild_id, player_discord_id, player_discord_id),
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


# ------------------------------------------------------------------- push notifications
PREF_KEYS = ("tournament_starts", "round_starts", "ace", "albatross",
             "top3_changes")


async def register_device(db_path, discord_id: str, push_token: str,
                          platform: str = "ios") -> None:
    await _execute(
        db_path,
        "INSERT INTO devices (discord_id, push_token, platform, updated_at)"
        " VALUES (?,?,?,?)"
        " ON CONFLICT(discord_id, push_token) DO UPDATE SET"
        " platform=excluded.platform, updated_at=excluded.updated_at",
        (discord_id, push_token, platform, utcnow_iso()),
    )


async def unregister_device(db_path, discord_id: str,
                            push_token: str) -> None:
    await _execute(
        db_path,
        "DELETE FROM devices WHERE discord_id = ? AND push_token = ?",
        (discord_id, push_token),
    )


async def get_notification_prefs(db_path, discord_id: str) -> dict:
    row = await _fetchone(
        db_path,
        "SELECT * FROM notification_prefs WHERE discord_id = ?",
        (discord_id,),
    )
    if row is None:
        return {k: True for k in PREF_KEYS}
    return {k: bool(row[k]) for k in PREF_KEYS}


async def set_notification_prefs(db_path, discord_id: str,
                                 prefs: dict) -> dict:
    clean = {k: (1 if prefs.get(k, True) else 0) for k in PREF_KEYS}
    await _execute(
        db_path,
        "INSERT INTO notification_prefs (discord_id, tournament_starts,"
        " round_starts, ace, albatross, top3_changes, updated_at)"
        " VALUES (?,?,?,?,?,?,?)"
        " ON CONFLICT(discord_id) DO UPDATE SET"
        " tournament_starts=excluded.tournament_starts,"
        " round_starts=excluded.round_starts, ace=excluded.ace,"
        " albatross=excluded.albatross, top3_changes=excluded.top3_changes,"
        " updated_at=excluded.updated_at",
        (discord_id, clean["tournament_starts"], clean["round_starts"],
         clean["ace"], clean["albatross"], clean["top3_changes"],
         utcnow_iso()),
    )
    return await get_notification_prefs(db_path, discord_id)


async def enqueue_push(db_path, discord_id: str, title: str, body: str,
                       data: dict | None = None) -> int:
    """Queue a push notification; returns the outbox row id."""
    lastrowid, _ = await _execute(
        db_path,
        "INSERT INTO push_outbox (discord_id, title, body, data_json,"
        " created_at) VALUES (?,?,?,?,?)",
        (discord_id, title, body, json.dumps(data or {}), utcnow_iso()),
    )
    return lastrowid


async def poll_push_outbox(db_path, limit: int = 50) -> list[dict]:
    rows = await _fetchall(
        db_path,
        "SELECT * FROM push_outbox WHERE sent_at IS NULL"
        " ORDER BY id ASC LIMIT ?",
        (limit,),
    )
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["data"] = json.loads(d.get("data_json") or "{}")
        except (ValueError, TypeError):
            d["data"] = {}
        out.append(d)
    return out


async def ack_push_outbox(db_path, ids: list[int]) -> None:
    if not ids:
        return
    now = utcnow_iso()
    await _execute(
        db_path,
        "UPDATE push_outbox SET sent_at = ? WHERE id IN (%s)"
        % ",".join("?" for _ in ids),
        (now, *ids),
    )


async def push_was_sent(db_path, kind: str, ref_id: str) -> bool:
    row = await _fetchone(
        db_path,
        "SELECT 1 FROM push_sent_log WHERE kind = ? AND ref_id = ?",
        (kind, ref_id),
    )
    return row is not None


async def mark_push_sent(db_path, kind: str, ref_id: str) -> None:
    await _execute(
        db_path,
        "INSERT OR IGNORE INTO push_sent_log (kind, ref_id, created_at)"
        " VALUES (?,?,?)",
        (kind, ref_id, utcnow_iso()),
    )


async def get_leaderboard_snapshot(db_path, tournament_id: str) -> list[str]:
    row = await _fetchone(
        db_path,
        "SELECT top3_json FROM leaderboard_snapshots WHERE tournament_id = ?",
        (tournament_id,),
    )
    if row is None:
        return []
    try:
        return json.loads(row["top3_json"] or "[]")
    except (ValueError, TypeError):
        return []


async def set_leaderboard_snapshot(db_path, tournament_id: str,
                                   top3: list[str]) -> None:
    await _execute(
        db_path,
        "INSERT INTO leaderboard_snapshots (tournament_id, top3_json,"
        " updated_at) VALUES (?,?,?)"
        " ON CONFLICT(tournament_id) DO UPDATE SET"
        " top3_json=excluded.top3_json, updated_at=excluded.updated_at",
        (tournament_id, json.dumps(top3), utcnow_iso()),
    )


async def notify_tournament_players(db_path, tournament_id: str, pref_key: str,
                                    title: str, body: str,
                                    data: dict | None = None,
                                    exclude: set[str] | None = None) -> int:
    """Enqueue a push for every registered player with the pref enabled.

    Returns the number of pushes enqueued.
    """
    rows = await _fetchall(
        db_path,
        "SELECT player_discord_id FROM registrations WHERE tournament_id = ?",
        (tournament_id,),
    )
    exclude = exclude or set()
    n = 0
    for r in rows:
        pid = r["player_discord_id"]
        if pid in exclude:
            continue
        prefs = await get_notification_prefs(db_path, pid)
        if not prefs.get(pref_key, True):
            continue
        dev = await _fetchone(
            db_path, "SELECT 1 FROM devices WHERE discord_id = ?", (pid,))
        if dev is None:
            continue
        await enqueue_push(db_path, pid, title, body, data)
        n += 1
    return n


# ------------------------------------------------------------------- casual tee times
async def create_casual_tee_time(
    db_path, creator_discord_id: str, label: str, course: str, pars: str,
    tee_position: str = "middle", pin_position: str = "white",
    wind_strength: str = "moderate", green_speed: str = "medium",
    starts_at: str = "", max_players: int = 4, notes: str = "",
    format: str = "stroke", matchplay_tee_time_id: str | None = None,
    altshot_tee_time_id: str | None = None,
) -> str:
    import uuid
    if format not in ("stroke", "best_ball", "match_play", "alt_shot"):
        raise ValueError(f"bad casual format: {format}")
    tt_id = uuid.uuid4().hex[:12]
    await _execute(
        db_path,
        "INSERT INTO casual_tee_times (id, creator_discord_id, label, course,"
        " pars, tee_position, pin_position, wind_strength, green_speed,"
        " starts_at, max_players, notes, format, matchplay_tee_time_id,"
        " altshot_tee_time_id, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (tt_id, creator_discord_id, label, course, pars, tee_position,
         pin_position, wind_strength, green_speed, starts_at, max_players,
         notes, format, matchplay_tee_time_id, altshot_tee_time_id,
         utcnow_iso()),
    )
    await _execute(
        db_path,
        "INSERT OR IGNORE INTO casual_tee_time_players"
        " (tee_time_id, discord_id, joined_at) VALUES (?,?,?)",
        (tt_id, creator_discord_id, utcnow_iso()),
    )
    return tt_id


async def list_casual_tee_times(db_path, upcoming_only: bool = True,
                                limit: int = 100) -> list[dict]:
    if upcoming_only:
        rows = await _fetchall(
            db_path,
            "SELECT * FROM casual_tee_times WHERE starts_at >= ?"
            " ORDER BY starts_at ASC LIMIT ?",
            (upcoming_bound_iso(), limit),
        )
    else:
        rows = await _fetchall(
            db_path,
            "SELECT * FROM casual_tee_times ORDER BY starts_at DESC LIMIT ?",
            (limit,),
        )
    out = []
    for r in rows:
        d = dict(r)
        d["players"] = await list_casual_tee_time_players(db_path, d["id"])
        out.append(d)
    return out


async def get_casual_tee_time(db_path, tt_id: str) -> dict | None:
    row = await _fetchone(
        db_path, "SELECT * FROM casual_tee_times WHERE id = ?", (tt_id,))
    if row is None:
        return None
    d = dict(row)
    d["players"] = await list_casual_tee_time_players(db_path, tt_id)
    return d


async def list_casual_tee_time_players(db_path, tt_id: str) -> list[str]:
    rows = await _fetchall(
        db_path,
        "SELECT discord_id FROM casual_tee_time_players"
        " WHERE tee_time_id = ? ORDER BY joined_at ASC",
        (tt_id,),
    )
    return [r["discord_id"] for r in rows]


async def join_casual_tee_time(db_path, tt_id: str,
                               discord_id: str) -> tuple[bool, str]:
    """No membership limits — a player may join any number of casual
    tee times. Returns (ok, reason)."""
    tt = await get_casual_tee_time(db_path, tt_id)
    if tt is None:
        return False, "not_found"
    if discord_id in tt["players"]:
        return True, "already_in"
    if len(tt["players"]) >= (tt["max_players"] or 4):
        return False, "full"
    await _execute(
        db_path,
        "INSERT OR IGNORE INTO casual_tee_time_players"
        " (tee_time_id, discord_id, joined_at) VALUES (?,?,?)",
        (tt_id, discord_id, utcnow_iso()),
    )
    return True, "joined"


async def leave_casual_tee_time(db_path, tt_id: str,
                                discord_id: str) -> None:
    await _execute(
        db_path,
        "DELETE FROM casual_tee_time_players"
        " WHERE tee_time_id = ? AND discord_id = ?",
        (tt_id, discord_id),
    )


async def update_casual_tee_time(db_path, tt_id: str,
                                 fields: dict) -> bool:
    allowed = {"label", "course", "pars", "tee_position", "pin_position",
               "wind_strength", "green_speed", "starts_at", "max_players",
               "notes", "format", "matchplay_tee_time_id",
               "altshot_tee_time_id"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return False
    cols = ", ".join(f"{k} = ?" for k in sets)
    _, rowcount = await _execute(
        db_path,
        f"UPDATE casual_tee_times SET {cols} WHERE id = ?",
        (*sets.values(), tt_id),
    )
    return rowcount > 0


async def delete_casual_tee_time(db_path, tt_id: str) -> None:
    tt = await get_casual_tee_time(db_path, tt_id)
    await _execute(db_path,
                   "DELETE FROM casual_tee_time_players WHERE tee_time_id = ?",
                   (tt_id,))
    # Casual scorecards (and their hole_shots via ON DELETE CASCADE).
    await _execute(db_path,
                   "DELETE FROM scorecards WHERE casual_tee_time_id = ?",
                   (tt_id,))
    await _execute(db_path, "DELETE FROM casual_tee_times WHERE id = ?",
                   (tt_id,))
    # Linked match-play / alt-shot games live and die with the casual round.
    if tt:
        if tt.get("matchplay_tee_time_id"):
            await delete_matchplay_tee_time(
                db_path, tt["matchplay_tee_time_id"])
        if tt.get("altshot_tee_time_id"):
            await delete_altshot_tee_time(db_path, tt["altshot_tee_time_id"])


# ------------------------------------------------------------------- alt-shot records
class AltShotError(Exception):
    """Domain errors for alt-shot flows: 'not_found', 'full', 'already_in',
    'no_team', 'not_on_team', 'no_guests', 'need_team', 'needs_players',
    'team_not_full', 'teams_not_full', 'teams_locked', 'too_many',
    'bad_team_size', 'bad_holes'."""


class TeamNameError(Exception):
    """Raised when a team name is taken by / belongs to another crew.
    The API maps this to a 409 with the message as detail."""


def normalize_team_name(name) -> str:
    """Canonical team-name key: strip, collapse internal whitespace,
    casefold. Blank/whitespace-only input normalizes to ''."""
    import re
    return re.sub(r"\s+", " ", (name or "").strip()).casefold()


async def _team_name_row(db_path, norm: str):
    return await _fetchone(
        db_path,
        "SELECT * FROM team_name_registry WHERE team_name = ?",
        (norm,))


async def team_name_claim(db_path, name, member_ids,
                          claim_ref: str) -> tuple:
    """Claim a team name for a roster.

    Returns (True, '') on success, (False, message) when another crew
    owns the name. Blank names never claim: (True, '').
    """
    import json
    norm = normalize_team_name(name)
    if not norm:
        return True, ""
    owners = sorted({str(x) for x in (member_ids or []) if x})
    row = await _team_name_row(db_path, norm)
    if row is None:
        await _execute(
            db_path,
            "INSERT INTO team_name_registry (team_name, display_name,"
            " owner_ids, is_final, pending_claim, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (norm, (name or "").strip(), json.dumps(owners), 0,
             claim_ref, utcnow_iso()),
        )
        return True, ""
    if row["pending_claim"] == claim_ref:
        # Same team re-affirming its own tentative name.
        return True, ""
    if row["is_final"]:
        try:
            registered = set(json.loads(row["owner_ids"] or "[]"))
        except (ValueError, TypeError):
            registered = set()
        if set(owners) <= registered:
            return True, ""
        return False, (f"Team name '{row['display_name']}' belongs to"
                       " another crew.")
    return False, (f"Team name '{row['display_name']}' is taken by"
                   " another crew.")


async def team_name_finalize(db_path, name, claim_ref: str,
                             member_ids) -> None:
    """Lock a tentatively-claimed name to its full roster. Only acts on
    a pending row held by this claim_ref — idempotent otherwise."""
    import json
    norm = normalize_team_name(name)
    if not norm:
        return
    row = await _team_name_row(db_path, norm)
    if row and not row["is_final"] and row["pending_claim"] == claim_ref:
        owners = sorted({str(x) for x in (member_ids or []) if x})
        await _execute(
            db_path,
            "UPDATE team_name_registry SET owner_ids = ?, is_final = 1,"
            " pending_claim = NULL WHERE team_name = ?",
            (json.dumps(owners), norm),
        )


async def team_name_release(db_path, claim_ref: str) -> None:
    """Release every still-pending name claimed by this team/side (used
    when the team is renamed away or deleted)."""
    await _execute(
        db_path,
        "DELETE FROM team_name_registry WHERE pending_claim = ?"
        " AND is_final = 0",
        (claim_ref,),
    )


async def team_name_rename(db_path, old_name, new_name, member_ids,
                           claim_ref: str) -> tuple:
    """Rename a team's name: claim the new name first; only on success
    release the old pending claim. Returns (True, '') or
    (False, message)."""
    if normalize_team_name(new_name) == normalize_team_name(old_name):
        return True, ""
    ok, msg = await team_name_claim(db_path, new_name, member_ids,
                                    claim_ref)
    if not ok:
        return False, msg
    old_norm = normalize_team_name(old_name)
    if old_norm:
        row = await _team_name_row(db_path, old_norm)
        if (row and not row["is_final"]
                and row["pending_claim"] == claim_ref):
            await _execute(
                db_path,
                "DELETE FROM team_name_registry WHERE team_name = ?",
                (old_norm,),
            )
    return True, ""


async def _altshot_members(db_path, team_id: str) -> list[dict]:
    return await _fetchall(
        db_path,
        "SELECT * FROM altshot_team_members WHERE team_id = ?"
        " ORDER BY position, created_at",
        (team_id,))


async def _add_altshot_member(db_path, team_id: str,
                              discord_id: str | None, name: str) -> None:
    import uuid
    members = await _altshot_members(db_path, team_id)
    pos = max([m["position"] for m in members], default=0) + 1
    await _execute(
        db_path,
        "INSERT INTO altshot_team_members (id, team_id, discord_id, name,"
        " position, created_at) VALUES (?,?,?,?,?,?)",
        (uuid.uuid4().hex[:12], team_id, discord_id, (name or "").strip(),
         pos, utcnow_iso()),
    )


async def _altshot_member_names(db_path, members: list[dict]) -> list[str]:
    names = []
    for m in members:
        if m["discord_id"]:
            player = await get_player(db_path, m["discord_id"])
            names.append(display_name_of(player, m["discord_id"]))
        else:
            names.append(m["name"])
    return names


async def create_altshot_tee_time(
    db_path, creator_discord_id: str, label: str, course: str, pars: str,
    tee_position: str = "middle", pin_position: str = "white",
    wind_strength: str = "moderate", green_speed: str = "medium",
    starts_at: str = "", max_teams: int = 2, team_size: int = 2,
    notes: str = "", casual_tee_time_id: str | None = None,
    # No team names in alt-shot: teams are identified by their players.
) -> str:
    import uuid
    if team_size not in (2, 3, 4):
        raise AltShotError("bad_team_size")
    tt_id = uuid.uuid4().hex[:12]
    team1_id = uuid.uuid4().hex[:12]
    team2_id = uuid.uuid4().hex[:12] if max_teams == 2 else None
    await _execute(
        db_path,
        "INSERT INTO altshot_tee_times (id, creator_discord_id, label, course,"
        " pars, tee_position, pin_position, wind_strength, green_speed,"
        " starts_at, max_teams, team_size, notes, casual_tee_time_id,"
        " created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (tt_id, creator_discord_id, label, course, pars, tee_position,
         pin_position, wind_strength, green_speed, starts_at, max_teams,
         team_size, notes, casual_tee_time_id, utcnow_iso()),
    )
    # Team 1 is created right away with the creator as the first roster
    # member. A 2-team tee time also pre-creates team 2 (empty — players
    # pick a team when they join).
    await _create_altshot_team(db_path, tt_id, creator_discord_id,
                               team_number=1,
                               team_id=team1_id)
    if team2_id:
        await _create_altshot_team(db_path, tt_id, None,
                                   team_number=2,
                                   team_id=team2_id)
    return tt_id


async def _create_altshot_team(db_path, tt_id: str, discord_id: str | None,
                               extra_names: list | None = None,
                               team_number: int = 1,
                               team_id: str | None = None) -> str:
    import uuid
    team_id = team_id or uuid.uuid4().hex[:12]
    await _execute(
        db_path,
        "INSERT INTO altshot_teams (id, tee_time_id, team_number,"
        " player1_discord_id, team_name, created_at)"
        " VALUES (?,?,?,?,?,?)",
        (team_id, tt_id, team_number, discord_id, "",
         utcnow_iso()),
    )
    if discord_id:
        player = await get_player(db_path, discord_id)
        await _add_altshot_member(
            db_path, team_id, discord_id,
            display_name_of(player, discord_id))
    for n in extra_names or []:
        if (n or "").strip():
            await _add_altshot_member(db_path, team_id, None, n)
    return team_id


async def _altshot_team_json(db_path, team: dict,
                             tt: dict | None = None) -> dict:
    members = await _altshot_members(db_path, team["id"])
    names = await _altshot_member_names(db_path, members)
    team_number = team.get("team_number") or 1
    two_team = bool(tt and tt.get("max_teams") == 2 and tt.get("team_size"))
    # No team names in alt-shot: two-team sides use the structural label,
    # one-team sides show the player roster.
    if two_team:
        display = f"Team {team_number}"
    else:
        display = " & ".join(names)
    score = await _fetchone(
        db_path, "SELECT * FROM altshot_scores WHERE team_id = ?",
        (team["id"],))
    score_json = None
    if score:
        import json
        score_json = {
            "holes": json.loads(score["holes"]),
            "total": score["total"],
            "submitted_by": score["submitted_by"],
            "submitted_at": score["submitted_at"],
        }
    p1_id = team.get("player1_discord_id")
    player = await get_player(db_path, p1_id) if p1_id else None
    return {
        "id": team["id"],
        "team_number": team_number,
        "player1_discord_id": p1_id or "",
        "player1_name": display_name_of(player, p1_id) if p1_id else "",
        "player_names": names,
        "member_discord_ids": [m["discord_id"] for m in members
                               if m["discord_id"]],
        "team_size": len(names),
        "display_name": display,
        "score": score_json,
    }


async def _altshot_tee_time_json(db_path, row: dict) -> dict:
    teams = await _fetchall(
        db_path,
        "SELECT * FROM altshot_teams WHERE tee_time_id = ?"
        " ORDER BY team_number, created_at",
        (row["id"],))
    return {
        "id": row["id"],
        "creator_discord_id": row["creator_discord_id"],
        "label": row["label"],
        "course": row["course"],
        "pars": row["pars"],
        "tee_position": row["tee_position"],
        "pin_position": row["pin_position"],
        "wind_strength": row["wind_strength"],
        "green_speed": row["green_speed"],
        "starts_at": row["starts_at"],
        "max_teams": row["max_teams"],
        "team_size": row.get("team_size"),
        "notes": row["notes"],
        "created_at": row["created_at"],
        "casual_tee_time_id": row.get("casual_tee_time_id"),
        "teams": [await _altshot_team_json(db_path, t, row) for t in teams],
    }


async def list_altshot_tee_times(db_path, upcoming_only: bool = True,
                                 limit: int = 100) -> list[dict]:
    if upcoming_only:
        rows = await _fetchall(
            db_path,
            "SELECT * FROM altshot_tee_times WHERE starts_at >= ?"
            " AND casual_tee_time_id IS NULL"
            " ORDER BY starts_at ASC LIMIT ?",
            (upcoming_bound_iso(), limit),
        )
    else:
        rows = await _fetchall(
            db_path,
            "SELECT * FROM altshot_tee_times WHERE casual_tee_time_id IS NULL"
            " ORDER BY starts_at DESC LIMIT ?",
            (limit,),
        )
    return [await _altshot_tee_time_json(db_path, r) for r in rows]


async def get_altshot_tee_time(db_path, tt_id: str) -> dict | None:
    row = await _fetchone(
        db_path, "SELECT * FROM altshot_tee_times WHERE id = ?", (tt_id,))
    if not row:
        return None
    return await _altshot_tee_time_json(db_path, row)


async def get_altshot_team(db_path, team_id: str) -> dict | None:
    return await _fetchone(
        db_path, "SELECT * FROM altshot_teams WHERE id = ?", (team_id,))


async def _altshot_member_team(db_path, tt_id: str,
                               discord_id: str) -> dict | None:
    """The team (in this tee time) that has discord_id on its roster."""
    return await _fetchone(
        db_path,
        "SELECT t.* FROM altshot_teams t"
        " JOIN altshot_team_members m ON m.team_id = t.id"
        " WHERE t.tee_time_id = ? AND m.discord_id = ?",
        (tt_id, discord_id),
    )


async def join_altshot_tee_time(db_path, tt_id: str, discord_id: str,
                                extra_names: list | None = None,
                                team_id: str | None = None) -> dict:
    """1-team tee times: the caller joins the single team's roster until it
    is full. 2-team tee times (new model, team_size set): the caller joins
    the team identified by team_id — registered players only, no guest
    names. Legacy 2-team tee times (team_size NULL): the caller's own team
    is created. No cross-tee-time limits."""
    tt = await _fetchone(
        db_path, "SELECT * FROM altshot_tee_times WHERE id = ?", (tt_id,))
    if not tt:
        raise AltShotError("not_found")
    if await _altshot_member_team(db_path, tt_id, discord_id):
        return await _altshot_tee_time_json(db_path, tt)  # already in
    if tt["max_teams"] == 1:
        # Fixed roster: join the one team.
        team = await _fetchone(
            db_path, "SELECT * FROM altshot_teams WHERE tee_time_id = ?",
            (tt_id,))
        if not team:
            await _create_altshot_team(db_path, tt_id, discord_id)
        else:
            members = await _altshot_members(db_path, team["id"])
            if len(members) >= tt["team_size"]:
                raise AltShotError("full")
            player = await get_player(db_path, discord_id)
            await _add_altshot_member(
                db_path, team["id"], discord_id,
                display_name_of(player, discord_id))
        return await get_altshot_tee_time(db_path, tt_id)
    if tt["team_size"]:
        # Fixed two teams: join the chosen team. Registered players only.
        if extra_names and any((n or "").strip() for n in extra_names):
            raise AltShotError("no_guests")
        if not team_id:
            raise AltShotError("need_team")
        team = await _fetchone(
            db_path, "SELECT * FROM altshot_teams WHERE id = ?"
            " AND tee_time_id = ?", (team_id, tt_id))
        if not team:
            raise AltShotError("not_found")
        members = await _altshot_members(db_path, team["id"])
        if len(members) >= tt["team_size"]:
            raise AltShotError("full")
        player = await get_player(db_path, discord_id)
        await _add_altshot_member(
            db_path, team["id"], discord_id,
            display_name_of(player, discord_id))
        return await get_altshot_tee_time(db_path, tt_id)
    # Flexible (legacy): the caller starts their own team.
    existing = await _fetchone(
        db_path,
        "SELECT * FROM altshot_teams WHERE tee_time_id = ?"
        " AND player1_discord_id = ?",
        (tt_id, discord_id),
    )
    if existing:
        return await _altshot_tee_time_json(db_path, tt)
    count = await _fetchone(
        db_path,
        "SELECT COUNT(*) AS n FROM altshot_teams WHERE tee_time_id = ?",
        (tt_id,))
    if count and count["n"] >= tt["max_teams"]:
        raise AltShotError("full")
    await _create_altshot_team(db_path, tt_id, discord_id,
                               extra_names=extra_names)
    return await get_altshot_tee_time(db_path, tt_id)


async def _altshot_any_score(db_path, tt_id: str) -> bool:
    """Whether any team in this tee time has submitted a score."""
    row = await _fetchone(
        db_path,
        "SELECT 1 FROM altshot_scores s"
        " JOIN altshot_teams t ON t.id = s.team_id"
        " WHERE t.tee_time_id = ? LIMIT 1",
        (tt_id,))
    return row is not None


async def switch_altshot_team(db_path, tt_id: str, discord_id: str,
                              team_id: str) -> dict:
    """Move the caller to another team of a 2-team (fixed) tee time —
    or join it if they are not on a team yet. Blocked once any score is
    submitted for the tee time (teams lock at scoring)."""
    tt = await _fetchone(
        db_path, "SELECT * FROM altshot_tee_times WHERE id = ?", (tt_id,))
    if not tt:
        raise AltShotError("not_found")
    if not (tt["max_teams"] == 2 and tt["team_size"]):
        raise AltShotError("not_found")
    team = await _fetchone(
        db_path, "SELECT * FROM altshot_teams WHERE id = ?"
        " AND tee_time_id = ?", (team_id, tt_id))
    if not team:
        raise AltShotError("not_found")
    current = await _altshot_member_team(db_path, tt_id, discord_id)
    if current and current["id"] == team["id"]:
        return await get_altshot_tee_time(db_path, tt_id)  # no-op
    if await _altshot_any_score(db_path, tt_id):
        raise AltShotError("teams_locked")
    members = await _altshot_members(db_path, team["id"])
    if len(members) >= tt["team_size"]:
        raise AltShotError("full")
    if current:
        await _execute(
            db_path,
            "DELETE FROM altshot_team_members WHERE team_id = ?"
            " AND discord_id = ?",
            (current["id"], discord_id),
        )
    player = await get_player(db_path, discord_id)
    await _add_altshot_member(
        db_path, team["id"], discord_id,
        display_name_of(player, discord_id))
    return await get_altshot_tee_time(db_path, tt_id)


async def manage_altshot_team(db_path, tt_id: str, team_id: str,
                              move_discord_id: str | None = None,
                              remove_discord_id: str | None = None) -> dict:
    """Organizer/crew management of a 2-team (fixed) tee time's team:
    move a player onto this team from the other team, or remove a player
    from this team. Moves and removals are blocked once any score is
    submitted for the tee time. No team names: teams are "Team 1"/"Team 2"
    identified by their players."""
    tt = await _fetchone(
        db_path, "SELECT * FROM altshot_tee_times WHERE id = ?", (tt_id,))
    if not tt:
        raise AltShotError("not_found")
    if not (tt["max_teams"] == 2 and tt["team_size"]):
        raise AltShotError("not_found")
    team = await _fetchone(
        db_path, "SELECT * FROM altshot_teams WHERE id = ?"
        " AND tee_time_id = ?", (team_id, tt_id))
    if not team:
        raise AltShotError("not_found")
    scored = await _altshot_any_score(db_path, tt_id)
    if move_discord_id:
        if scored:
            raise AltShotError("teams_locked")
        other = await _altshot_member_team(db_path, tt_id, move_discord_id)
        if not other:
            raise AltShotError("not_on_team")
        if other["id"] != team["id"]:
            members = await _altshot_members(db_path, team["id"])
            if len(members) >= tt["team_size"]:
                raise AltShotError("full")
            await _execute(
                db_path,
                "DELETE FROM altshot_team_members WHERE team_id = ?"
                " AND discord_id = ?",
                (other["id"], move_discord_id),
            )
            player = await get_player(db_path, move_discord_id)
            await _add_altshot_member(
                db_path, team["id"], move_discord_id,
                display_name_of(player, move_discord_id))
    if remove_discord_id:
        if scored:
            raise AltShotError("teams_locked")
        row = await _fetchone(
            db_path,
            "SELECT id FROM altshot_team_members WHERE team_id = ?"
            " AND discord_id = ?",
            (team["id"], remove_discord_id))
        if not row:
            raise AltShotError("not_on_team")
        await _execute(
            db_path, "DELETE FROM altshot_team_members WHERE id = ?",
            (row["id"],))
    return await get_altshot_tee_time(db_path, tt_id)


async def leave_altshot_tee_time(db_path, tt_id: str,
                                 discord_id: str) -> dict | None:
    tt = await _fetchone(
        db_path, "SELECT * FROM altshot_tee_times WHERE id = ?", (tt_id,))
    if not tt:
        return None
    if tt["max_teams"] == 1:
        # Fixed roster: just remove the caller's roster spot.
        team = await _altshot_member_team(db_path, tt_id, discord_id)
        if not team:
            return await _altshot_tee_time_json(db_path, tt)
        await _execute(
            db_path,
            "DELETE FROM altshot_team_members WHERE team_id = ?"
            " AND discord_id = ?",
            (team["id"], discord_id),
        )
        remaining = await _altshot_members(db_path, team["id"])
        if not remaining:
            await _execute(db_path,
                           "DELETE FROM altshot_scores WHERE team_id = ?",
                           (team["id"],))
            await _execute(db_path, "DELETE FROM altshot_teams WHERE id = ?",
                           (team["id"],))
        return await get_altshot_tee_time(db_path, tt_id)
    if tt["team_size"]:
        # Fixed two teams: remove the caller's roster spot; the team
        # itself stays (it was pre-created).
        team = await _altshot_member_team(db_path, tt_id, discord_id)
        if not team:
            return await _altshot_tee_time_json(db_path, tt)
        await _execute(
            db_path,
            "DELETE FROM altshot_team_members WHERE team_id = ?"
            " AND discord_id = ?",
            (team["id"], discord_id),
        )
        return await get_altshot_tee_time(db_path, tt_id)
    # Flexible (legacy): leaving removes the caller's whole team.
    team = await _fetchone(
        db_path,
        "SELECT * FROM altshot_teams WHERE tee_time_id = ?"
        " AND player1_discord_id = ?",
        (tt_id, discord_id),
    )
    if not team:
        return await _altshot_tee_time_json(db_path, tt)
    await _execute(db_path, "DELETE FROM altshot_scores WHERE team_id = ?",
                   (team["id"],))
    await _execute(
        db_path, "DELETE FROM altshot_team_members WHERE team_id = ?",
        (team["id"],))
    await _execute(db_path, "DELETE FROM altshot_teams WHERE id = ?",
                   (team["id"],))
    return await get_altshot_tee_time(db_path, tt_id)


async def update_altshot_team(db_path, team_id: str,
                              fields: dict) -> dict | None:
    team = await get_altshot_team(db_path, team_id)
    if not team:
        return None
    # No team names in alt-shot: the "team_name" field is never written.
    if "extra_names" in fields:
        tt = await _fetchone(
            db_path, "SELECT * FROM altshot_tee_times WHERE id = ?",
            (team["tee_time_id"],))
        extras = [(n or "").strip() for n in (fields["extra_names"] or [])]
        extras = [n for n in extras if n]
        if tt and tt["max_teams"] == 2 and tt["team_size"] and extras:
            # Fixed two teams: registered players only — no typed-in
            # guest names.
            raise AltShotError("no_guests")
        cap = tt["team_size"] if tt and tt["team_size"] else 4
        members = await _altshot_members(db_path, team_id)
        registered = [m for m in members if m["discord_id"]]
        if len(registered) + len(extras) > cap:
            raise AltShotError("too_many")
        await _execute(
            db_path,
            "DELETE FROM altshot_team_members WHERE team_id = ?"
            " AND discord_id IS NULL",
            (team_id,),
        )
        for n in extras:
            await _add_altshot_member(db_path, team_id, None, n)
    return await get_altshot_team(db_path, team_id)


async def update_altshot_tee_time(db_path, tt_id: str,
                                  fields: dict) -> dict | None:
    tt = await _fetchone(
        db_path, "SELECT * FROM altshot_tee_times WHERE id = ?", (tt_id,))
    if not tt:
        return None
    allowed = {"label", "course", "tee_position", "pin_position",
               "wind_strength", "green_speed", "starts_at", "max_teams",
               "team_size", "notes", "pars"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    new_size = updates.get("team_size", tt["team_size"])
    if new_size is not None and new_size not in (2, 3, 4):
        raise AltShotError("bad_team_size")
    if new_size is not None:
        # No team may already have more players than the new roster size.
        teams = await _fetchall(
            db_path, "SELECT id FROM altshot_teams WHERE tee_time_id = ?",
            (tt_id,))
        for t in teams:
            if len(await _altshot_members(db_path, t["id"])) > new_size:
                raise AltShotError("too_many")
    if updates:
        sets = [f"{k} = ?" for k in updates]
        await _execute(
            db_path,
            f"UPDATE altshot_tee_times SET {', '.join(sets)} WHERE id = ?",
            tuple(updates[k] for k in updates) + (tt_id,),
        )
    return await get_altshot_tee_time(db_path, tt_id)


async def delete_altshot_tee_time(db_path, tt_id: str) -> None:
    team_ids = await _fetchall(
        db_path, "SELECT id FROM altshot_teams WHERE tee_time_id = ?",
        (tt_id,))
    for t in team_ids:
        await _execute(db_path, "DELETE FROM altshot_scores WHERE team_id = ?",
                       (t["id"],))
        await _execute(db_path,
                       "DELETE FROM altshot_team_members WHERE team_id = ?",
                       (t["id"],))
    await _execute(db_path, "DELETE FROM altshot_teams WHERE tee_time_id = ?",
                   (tt_id,))
    await _execute(db_path, "DELETE FROM altshot_tee_times WHERE id = ?",
                   (tt_id,))


async def submit_altshot_score(db_path, team_id: str, holes: list,
                               submitted_by: str) -> dict:
    """Submit (or re-submit) a team's 18-hole score. Not live — a single
    stored card per team. 1-team tee times need a full roster; 2-team
    (fixed) tee times need both teams full; legacy flexible teams need at
    least 2 players."""
    import json
    team = await get_altshot_team(db_path, team_id)
    if not team:
        raise AltShotError("not_found")
    tt = await _fetchone(
        db_path, "SELECT * FROM altshot_tee_times WHERE id = ?",
        (team["tee_time_id"],))
    roster = await _altshot_members(db_path, team_id)
    if tt and tt["team_size"]:
        if tt["max_teams"] == 2:
            teams = await _fetchall(
                db_path, "SELECT id FROM altshot_teams WHERE tee_time_id = ?",
                (tt["id"],))
            for t in teams:
                if len(await _altshot_members(db_path, t["id"])) \
                        != tt["team_size"]:
                    raise AltShotError("teams_not_full")
        elif len(roster) != tt["team_size"]:
            raise AltShotError("team_not_full")
    elif len(roster) < 2:
        raise AltShotError("needs_players")
    if (not isinstance(holes, list) or len(holes) != 18
            or any(not isinstance(h, int) or h < 1 or h > 20
                   for h in holes)):
        raise AltShotError("bad_holes")
    total = sum(holes)
    now = utcnow_iso()
    await _execute(
        db_path,
        "INSERT INTO altshot_scores (team_id, holes, total, submitted_by,"
        " submitted_at) VALUES (?,?,?,?,?)"
        " ON CONFLICT(team_id) DO UPDATE SET holes = excluded.holes,"
        " total = excluded.total, submitted_by = excluded.submitted_by,"
        " submitted_at = excluded.submitted_at",
        (team_id, json.dumps(holes), total, submitted_by, now),
    )
    return {"team_id": team_id, "holes": holes, "total": total,
            "submitted_by": submitted_by, "submitted_at": now}


async def delete_altshot_score(db_path, team_id: str) -> None:
    await _execute(db_path, "DELETE FROM altshot_scores WHERE team_id = ?",
                   (team_id,))


async def get_altshot_score(db_path, team_id: str) -> dict | None:
    """The submitted score row for a team, or None if not yet submitted."""
    return await _fetchone(
        db_path, "SELECT * FROM altshot_scores WHERE team_id = ?",
        (team_id,))


async def altshot_team_member_ids(db_path, team_id: str) -> list[str]:
    """Registered (Discord) member ids on a team's roster."""
    members = await _altshot_members(db_path, team_id)
    return [m["discord_id"] for m in members if m["discord_id"]]


async def get_altshot_records(db_path, course: str,
                              team_size: int,
                              tee_position: str | None = None,
                              pin_position: str | None = None,
                              wind_strength: str | None = None,
                              green_speed: str | None = None) -> list[dict]:
    """Per-course, per-team-size, per-setup record leaderboard: all submitted
    team scores for that roster size whose tee time matches the given
    setup variables, best first. No notifications are ever sent for
    records."""
    clauses = ["tt.course = ?"]
    params: list = [course]
    for col, val in (("tt.tee_position", tee_position),
                     ("tt.pin_position", pin_position),
                     ("tt.wind_strength", wind_strength),
                     ("tt.green_speed", green_speed)):
        if val:
            clauses.append(f"{col} = ?")
            params.append(val)
    clauses.append(
        "(SELECT COUNT(*) FROM altshot_team_members m"
        " WHERE m.team_id = t.id) = ?")
    params.append(team_size)
    rows = await _fetchall(
        db_path,
        "SELECT s.*, t.player1_discord_id,"
        " tt.course, tt.pars, tt.label AS tt_label,"
        " tt.tee_position, tt.pin_position, tt.wind_strength,"
        " tt.green_speed"
        " FROM altshot_scores s"
        " JOIN altshot_teams t ON t.id = s.team_id"
        " JOIN altshot_tee_times tt ON tt.id = t.tee_time_id"
        f" WHERE {' AND '.join(clauses)}"
        " ORDER BY s.total ASC, s.submitted_at ASC",
        tuple(params),
    )
    records = []
    for r in rows:
        members = await _altshot_members(db_path, r["team_id"])
        names = await _altshot_member_names(db_path, members)
        try:
            pars = [int(x) for x in (r["pars"] or "").split(",") if x]
            to_par = r["total"] - sum(pars) if len(pars) == 18 else None
        except (ValueError, TypeError):
            to_par = None
        records.append({
            "course": r["course"],
            "total": r["total"],
            "to_par": to_par,
            "team_size": team_size,
            # No team names: records are keyed/displayed by player roster.
            "team_display": " & ".join(names),
            "player_names": names,
            "holes": _json_list(r["holes"]),
            "pars": [int(x) for x in (r["pars"] or "").split(",") if x],
            "tee_time_label": r["tt_label"],
            "tee_position": r["tee_position"],
            "pin_position": r["pin_position"],
            "wind_strength": r["wind_strength"],
            "green_speed": r["green_speed"],
            "submitted_at": r["submitted_at"],
        })
    return records


async def get_altshot_records_summary(
        db_path, team_size: int,
        tee_position: str | None = None,
        pin_position: str | None = None,
        wind_strength: str | None = None,
        green_speed: str | None = None) -> dict:
    """Best (lowest-total, earliest-submitted on ties) submitted alt-shot
    score per course for one roster size and setup — a single batched
    lookup for course pickers. Returns {course: record} with total,
    to_par, and the holding team's display name."""
    clauses = []
    params: list = []
    for col, val in (("tt.tee_position", tee_position),
                     ("tt.pin_position", pin_position),
                     ("tt.wind_strength", wind_strength),
                     ("tt.green_speed", green_speed)):
        if val:
            clauses.append(f"{col} = ?")
            params.append(val)
    clauses.append(
        "(SELECT COUNT(*) FROM altshot_team_members m"
        " WHERE m.team_id = t.id) = ?")
    params.append(team_size)
    rows = await _fetchall(
        db_path,
        "SELECT tt.course, s.total, s.team_id, s.submitted_at,"
        " tt.pars"
        " FROM altshot_scores s"
        " JOIN altshot_teams t ON t.id = s.team_id"
        " JOIN altshot_tee_times tt ON tt.id = t.tee_time_id"
        f" WHERE {' AND '.join(clauses)}"
        " ORDER BY tt.course ASC, s.total ASC, s.submitted_at ASC",
        tuple(params),
    )
    best: dict = {}
    for r in rows:
        course = r["course"]
        if course in best:
            continue  # rows are ordered best-first per course
        members = await _altshot_members(db_path, r["team_id"])
        names = await _altshot_member_names(db_path, members)
        try:
            pars = [int(x) for x in (r["pars"] or "").split(",") if x]
            to_par = r["total"] - sum(pars) if len(pars) == 18 else None
        except (ValueError, TypeError):
            to_par = None
        best[course] = {
            "course": course,
            "total": r["total"],
            "to_par": to_par,
            # No team names: the holding team is shown by player roster.
            "team_display": " & ".join(names),
        }
    return best


# ---------------------------------------------------------------- match-play
class MatchPlayError(Exception):
    """Match-play domain errors. str(e) is one of:
    'not_found', 'bad_format', 'bad_team_size', 'bad_side', 'full',
    'already_in', 'sides_not_full', 'bad_results'."""


def matchplay_outcome(hole_results: list) -> dict:
    """Pure match-play result computation from an 18-list of nullable ints
    (1 = side 1 wins the hole, -1 = side 2 wins, 0 = halved).

    lead is side-1's net holes won; remaining = holes not yet played."""
    played = sum(1 for r in hole_results if r is not None)
    lead = sum(r for r in hole_results if r is not None)
    remaining = 18 - played
    base = {"lead": lead, "played": played, "remaining": remaining}
    if remaining > 0:
        if lead > remaining:
            return {**base, "status": "completed", "winner_side": 1,
                    "result_text": f"{lead}&{remaining}"}
        if -lead > remaining:
            return {**base, "status": "completed", "winner_side": 2,
                    "result_text": f"{-lead}&{remaining}"}
    if played == 18:
        if lead > 0:
            return {**base, "status": "completed", "winner_side": 1,
                    "result_text": f"{lead} UP"}
        if lead < 0:
            return {**base, "status": "completed", "winner_side": 2,
                    "result_text": f"{-lead} UP"}
        return {**base, "status": "completed", "winner_side": None,
                "result_text": "All Square"}
    return {**base, "status": "in_progress", "winner_side": None,
            "result_text": ""}


def matchplay_live_text(outcome: dict, side1_name: str,
                        side2_name: str) -> str:
    """Human status line. Completed matches get a result banner
    ('Alumec wins 3&2' / 'All Square'); live matches get the UP / Dormie /
    All Square line."""
    if outcome["status"] == "completed":
        if outcome["winner_side"] == 1:
            return f"{side1_name} wins {outcome['result_text']}"
        if outcome["winner_side"] == 2:
            return f"{side2_name} wins {outcome['result_text']}"
        return "All Square"
    lead, remaining = outcome["lead"], outcome["remaining"]
    if lead == 0:
        return "All Square"
    if remaining > 0 and abs(lead) == remaining:
        return "Dormie"
    if lead > 0:
        return f"{side1_name} {lead} UP"
    return f"{side2_name} {-lead} UP"


async def _matchplay_side_members(db_path, side_id: str) -> list[dict]:
    return await _fetchall(
        db_path,
        "SELECT * FROM matchplay_side_members WHERE side_id = ?"
        " ORDER BY created_at",
        (side_id,))


async def _matchplay_member_names(db_path, members: list[dict]) -> list[str]:
    names = []
    for m in members:
        player = await get_player(db_path, m["discord_id"])
        names.append(display_name_of(player, m["discord_id"]))
    return names


async def _matchplay_side_json(db_path, side: dict) -> dict:
    members = await _matchplay_side_members(db_path, side["id"])
    names = await _matchplay_member_names(db_path, members)
    # Sides are identified purely by their players' names — no team names.
    return {
        "id": side["id"],
        "side_number": side["side_number"],
        "member_discord_ids": [m["discord_id"] for m in members],
        "member_names": names,
        "size": len(names),
        "display_name": " & ".join(names),
    }


async def _matchplay_score_json(db_path, tt: dict,
                                score: dict | None) -> dict | None:
    """Score row enriched with computed outcome fields and the live-text
    banner. None when no score has been saved yet."""
    if not score:
        return None
    import json
    sides = await _fetchall(
        db_path,
        "SELECT * FROM matchplay_sides WHERE tee_time_id = ?"
        " ORDER BY side_number",
        (tt["id"],))
    side_jsons = [await _matchplay_side_json(db_path, s) for s in sides]
    s1 = side_jsons[0]["display_name"] if side_jsons else "Side 1"
    s2 = (side_jsons[1]["display_name"] if len(side_jsons) > 1
          else "Side 2")
    results = json.loads(score["hole_results"])
    outcome = matchplay_outcome(results)
    return {
        "hole_results": results,
        "status": score["status"],
        "winner_side": score["winner_side"],
        "result_text": score["result_text"],
        "lead": outcome["lead"],
        "played": outcome["played"],
        "remaining": outcome["remaining"],
        "live_text": matchplay_live_text(
            {"status": score["status"],
             "winner_side": score["winner_side"],
             "result_text": score["result_text"],
             "lead": outcome["lead"],
             "played": outcome["played"],
             "remaining": outcome["remaining"]},
            s1 or "Side 1", s2 or "Side 2"),
        "submitted_by": score["submitted_by"],
        "submitted_at": score["submitted_at"],
    }


async def _matchplay_tee_time_json(db_path, row: dict) -> dict:
    sides = await _fetchall(
        db_path,
        "SELECT * FROM matchplay_sides WHERE tee_time_id = ?"
        " ORDER BY side_number",
        (row["id"],))
    side_jsons = [await _matchplay_side_json(db_path, s) for s in sides]
    cap = 1 if row["format"] == "single" else (row["team_size"] or 2)
    score = await _fetchone(
        db_path, "SELECT * FROM matchplay_scores WHERE tee_time_id = ?",
        (row["id"],))
    return {
        "id": row["id"],
        "creator_discord_id": row["creator_discord_id"],
        "label": row["label"],
        "course": row["course"],
        "pars": row["pars"],
        "tee_position": row["tee_position"],
        "pin_position": row["pin_position"],
        "wind_strength": row["wind_strength"],
        "green_speed": row["green_speed"],
        "starts_at": row["starts_at"],
        "format": row["format"],
        "team_size": row["team_size"],
        "side_cap": cap,
        "notes": row["notes"],
        "created_at": row["created_at"],
        "casual_tee_time_id": row.get("casual_tee_time_id"),
        "sides": side_jsons,
        "both_full": all(s["size"] >= cap for s in side_jsons)
        and len(side_jsons) == 2,
        "score": await _matchplay_score_json(db_path, row, score),
    }


async def create_matchplay_tee_time(
    db_path, creator_discord_id: str, label: str, course: str, pars: str,
    tee_position: str = "back", pin_position: str = "black",
    wind_strength: str = "moderate", green_speed: str = "pro",
    starts_at: str = "", format: str = "single", team_size: int = 1,
    notes: str = "", casual_tee_time_id: str | None = None,
) -> str:
    import uuid
    if format not in ("single", "bestball"):
        raise MatchPlayError("bad_format")
    if format == "single":
        if team_size not in (1, None):
            raise MatchPlayError("bad_team_size")
        team_size = 1
    elif team_size not in (2, 3, 4):
        raise MatchPlayError("bad_team_size")
    tt_id = uuid.uuid4().hex[:12]
    side1_id = uuid.uuid4().hex[:12]
    side2_id = uuid.uuid4().hex[:12]
    # No team names in matchplay: sides are identified by their players.
    await _execute(
        db_path,
        "INSERT INTO matchplay_tee_times (id, creator_discord_id, label,"
        " course, pars, tee_position, pin_position, wind_strength,"
        " green_speed, starts_at, format, team_size, notes, casual_tee_time_id,"
        " created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (tt_id, creator_discord_id, label, course, pars, tee_position,
         pin_position, wind_strength, green_speed, starts_at, format,
         team_size, notes, casual_tee_time_id, utcnow_iso()),
    )
    for n, side_id in ((1, side1_id), (2, side2_id)):
        await _execute(
            db_path,
            "INSERT INTO matchplay_sides (id, tee_time_id, side_number,"
            " team_name, created_at) VALUES (?,?,?,?,?)",
            (side_id, tt_id, n, "", utcnow_iso()),
        )
        if n == 1:
            # The creator takes side 1's first spot.
            await _execute(
                db_path,
                "INSERT INTO matchplay_side_members (id, side_id,"
                " discord_id, created_at) VALUES (?,?,?,?)",
                (uuid.uuid4().hex[:12], side_id, creator_discord_id,
                 utcnow_iso()),
            )
    return tt_id


async def list_matchplay_tee_times(db_path, upcoming_only: bool = True,
                                   limit: int = 100) -> list[dict]:
    if upcoming_only:
        rows = await _fetchall(
            db_path,
            "SELECT * FROM matchplay_tee_times WHERE starts_at >= ?"
            " AND casual_tee_time_id IS NULL"
            " ORDER BY starts_at ASC LIMIT ?",
            (upcoming_bound_iso(), limit),
        )
    else:
        rows = await _fetchall(
            db_path,
            "SELECT * FROM matchplay_tee_times WHERE casual_tee_time_id IS NULL"
            " ORDER BY starts_at DESC LIMIT ?",
            (limit,),
        )
    return [await _matchplay_tee_time_json(db_path, r) for r in rows]


async def get_matchplay_tee_time(db_path, tt_id: str) -> dict | None:
    row = await _fetchone(
        db_path, "SELECT * FROM matchplay_tee_times WHERE id = ?",
        (tt_id,))
    if not row:
        return None
    return await _matchplay_tee_time_json(db_path, row)


async def _matchplay_member_side(db_path, tt_id: str,
                                 discord_id: str) -> dict | None:
    """The side (in this tee time) that has discord_id on its roster."""
    return await _fetchone(
        db_path,
        "SELECT s.* FROM matchplay_sides s"
        " JOIN matchplay_side_members m ON m.side_id = s.id"
        " WHERE s.tee_time_id = ? AND m.discord_id = ?",
        (tt_id, discord_id),
    )


async def matchplay_member_ids(db_path, tt_id: str) -> list[str]:
    rows = await _fetchall(
        db_path,
        "SELECT m.discord_id FROM matchplay_side_members m"
        " JOIN matchplay_sides s ON s.id = m.side_id"
        " WHERE s.tee_time_id = ?",
        (tt_id,))
    return [r["discord_id"] for r in rows]


async def join_matchplay_tee_time(db_path, tt_id: str, discord_id: str,
                                  side_number: int) -> dict:
    tt = await _fetchone(
        db_path, "SELECT * FROM matchplay_tee_times WHERE id = ?", (tt_id,))
    if not tt:
        raise MatchPlayError("not_found")
    if side_number not in (1, 2):
        raise MatchPlayError("bad_side")
    if await _matchplay_member_side(db_path, tt_id, discord_id):
        raise MatchPlayError("already_in")
    side = await _fetchone(
        db_path,
        "SELECT * FROM matchplay_sides WHERE tee_time_id = ?"
        " AND side_number = ?",
        (tt_id, side_number))
    if not side:
        raise MatchPlayError("not_found")
    members = await _matchplay_side_members(db_path, side["id"])
    cap = 1 if tt["format"] == "single" else (tt["team_size"] or 2)
    if len(members) >= cap:
        raise MatchPlayError("full")
    import uuid
    await _execute(
        db_path,
        "INSERT INTO matchplay_side_members (id, side_id, discord_id,"
        " created_at) VALUES (?,?,?,?)",
        (uuid.uuid4().hex[:12], side["id"], discord_id, utcnow_iso()),
    )
    return await get_matchplay_tee_time(db_path, tt_id)


async def leave_matchplay_tee_time(db_path, tt_id: str,
                                   discord_id: str) -> dict | None:
    tt = await _fetchone(
        db_path, "SELECT * FROM matchplay_tee_times WHERE id = ?", (tt_id,))
    if not tt:
        return None
    side = await _matchplay_member_side(db_path, tt_id, discord_id)
    if not side:
        return await _matchplay_tee_time_json(db_path, tt)
    await _execute(
        db_path,
        "DELETE FROM matchplay_side_members WHERE side_id = ?"
        " AND discord_id = ?",
        (side["id"], discord_id),
    )
    return await get_matchplay_tee_time(db_path, tt_id)


async def update_matchplay_tee_time(db_path, tt_id: str,
                                    fields: dict) -> dict | None:
    tt = await _fetchone(
        db_path, "SELECT * FROM matchplay_tee_times WHERE id = ?", (tt_id,))
    if not tt:
        return None
    allowed = {"label", "course", "pars", "tee_position", "pin_position",
               "wind_strength", "green_speed", "starts_at", "notes"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if updates:
        sets = [f"{k} = ?" for k in updates]
        await _execute(
            db_path,
            f"UPDATE matchplay_tee_times SET {', '.join(sets)} WHERE id = ?",
            tuple(updates[k] for k in updates) + (tt_id,),
        )
    # No team names in matchplay: side names are never renamed.
    return await get_matchplay_tee_time(db_path, tt_id)


async def delete_matchplay_tee_time(db_path, tt_id: str) -> None:
    side_ids = await _fetchall(
        db_path, "SELECT id FROM matchplay_sides WHERE tee_time_id = ?",
        (tt_id,))
    for s in side_ids:
        await _execute(
            db_path, "DELETE FROM matchplay_side_members WHERE side_id = ?",
            (s["id"],))
    await _execute(db_path, "DELETE FROM matchplay_sides WHERE tee_time_id = ?",
                   (tt_id,))
    await _execute(db_path, "DELETE FROM matchplay_scores WHERE tee_time_id = ?",
                   (tt_id,))
    await _execute(db_path, "DELETE FROM matchplay_tee_times WHERE id = ?",
                   (tt_id,))


def _matchplay_validate_results(hole_results) -> list:
    if (not isinstance(hole_results, list) or len(hole_results) != 18
            or any(r is not None and r not in (-1, 0, 1)
                   for r in hole_results)):
        raise MatchPlayError("bad_results")
    return list(hole_results)


async def _matchplay_sides_full(db_path, tt: dict) -> bool:
    sides = await _fetchall(
        db_path,
        "SELECT * FROM matchplay_sides WHERE tee_time_id = ?"
        " ORDER BY side_number",
        (tt["id"],))
    if len(sides) != 2:
        return False
    cap = 1 if tt["format"] == "single" else (tt["team_size"] or 2)
    for s in sides:
        members = await _matchplay_side_members(db_path, s["id"])
        if len(members) < cap:
            return False
    return True


async def _matchplay_apply_records(db_path, tt: dict, winner_side: int | None,
                                   delta: int) -> None:
    """Apply (+1) or un-apply (-1) W-L-T tallies for a completed match.
    Tallies are keyed by the tee time's format ('single' | 'bestball').
    Called with +1 when a match completes and -1 when a previous completion
    is undone (score edited back to in-progress, changed, or cleared)."""
    import uuid
    sides = await _fetchall(
        db_path,
        "SELECT * FROM matchplay_sides WHERE tee_time_id = ?"
        " ORDER BY side_number",
        (tt["id"],))
    if len(sides) != 2:
        return
    rosters = []
    for s in sides:
        members = await _matchplay_side_members(db_path, s["id"])
        rosters.append(members)
    fmt = (tt.get("format") or "single").strip().lower()
    for idx, members in enumerate(rosters):
        side_no = idx + 1
        if winner_side is None:
            col = "ties"
        elif winner_side == side_no:
            col = "wins"
        else:
            col = "losses"
        for m in members:
            player = await get_player(db_path, m["discord_id"])
            name = display_name_of(player, m["discord_id"])
            await _execute(
                db_path,
                "INSERT INTO matchplay_records (id, format, discord_id,"
                " player_name, wins, losses, ties)"
                " VALUES (?,?,?,?,?,?,?)"
                " ON CONFLICT (format, discord_id)"
                " DO UPDATE SET player_name = excluded.player_name,"
                f" {col} = {col} + ?",
                (uuid.uuid4().hex[:12], fmt, m["discord_id"], name,
                 1 if col == "wins" else 0, 1 if col == "losses" else 0,
                 1 if col == "ties" else 0, delta),
            )


async def get_matchplay_score(db_path, tt_id: str) -> dict | None:
    return await _fetchone(
        db_path, "SELECT * FROM matchplay_scores WHERE tee_time_id = ?",
        (tt_id,))


async def matchplay_score_json(db_path, tt_id: str) -> dict | None:
    """The enriched score payload for a tee time (None if never saved)."""
    tt = await _fetchone(
        db_path, "SELECT * FROM matchplay_tee_times WHERE id = ?", (tt_id,))
    if not tt:
        return None
    score = await get_matchplay_score(db_path, tt_id)
    return await _matchplay_score_json(db_path, tt, score)


async def save_matchplay_score(db_path, tt_id: str, hole_results: list,
                               submitted_by: str) -> dict:
    """Save (live) hole results. Both sides must be full. Computes the
    outcome server-side; completing the match applies W-L-T records.
    Re-saving a completed match first un-applies the old result's records
    (the API layer gates that to mod/admin)."""
    import json
    tt = await _fetchone(
        db_path, "SELECT * FROM matchplay_tee_times WHERE id = ?", (tt_id,))
    if not tt:
        raise MatchPlayError("not_found")
    results = _matchplay_validate_results(hole_results)
    if not await _matchplay_sides_full(db_path, tt):
        raise MatchPlayError("sides_not_full")
    outcome = matchplay_outcome(results)
    existing = await get_matchplay_score(db_path, tt_id)
    if existing and existing["status"] == "completed":
        await _matchplay_apply_records(
            db_path, tt, existing["winner_side"], -1)
    now = utcnow_iso()
    await _execute(
        db_path,
        "INSERT INTO matchplay_scores (tee_time_id, hole_results, status,"
        " winner_side, result_text, submitted_by, submitted_at)"
        " VALUES (?,?,?,?,?,?,?)"
        " ON CONFLICT(tee_time_id) DO UPDATE SET"
        " hole_results = excluded.hole_results,"
        " status = excluded.status, winner_side = excluded.winner_side,"
        " result_text = excluded.result_text,"
        " submitted_by = excluded.submitted_by,"
        " submitted_at = excluded.submitted_at",
        (tt_id, json.dumps(results), outcome["status"],
         outcome["winner_side"], outcome["result_text"], submitted_by, now),
    )
    if outcome["status"] == "completed":
        await _matchplay_apply_records(
            db_path, tt, outcome["winner_side"], +1)
    score = await get_matchplay_score(db_path, tt_id)
    return await _matchplay_score_json(db_path, tt, score)


async def delete_matchplay_score(db_path, tt_id: str) -> None:
    """Clear a score back to 18 nulls / in_progress. Un-applies W-L-T
    records when the score had completed the match (the API layer gates
    that case to mod/admin)."""
    import json
    tt = await _fetchone(
        db_path, "SELECT * FROM matchplay_tee_times WHERE id = ?", (tt_id,))
    if not tt:
        raise MatchPlayError("not_found")
    existing = await get_matchplay_score(db_path, tt_id)
    if existing and existing["status"] == "completed":
        await _matchplay_apply_records(
            db_path, tt, existing["winner_side"], -1)
    await _execute(
        db_path,
        "INSERT INTO matchplay_scores (tee_time_id, hole_results, status,"
        " winner_side, result_text, submitted_by, submitted_at)"
        " VALUES (?,?,?,?,?,?,?)"
        " ON CONFLICT(tee_time_id) DO UPDATE SET"
        " hole_results = excluded.hole_results,"
        " status = excluded.status, winner_side = excluded.winner_side,"
        " result_text = excluded.result_text,"
        " submitted_by = excluded.submitted_by,"
        " submitted_at = excluded.submitted_at",
        (tt_id, json.dumps([None] * 18), "in_progress", None, "",
         "", utcnow_iso()),
    )


async def get_matchplay_records(db_path, format: str) -> list[dict]:
    """Per-format ('single' | 'bestball') player W-L-T leaderboard,
    wins first."""
    rows = await _fetchall(
        db_path,
        "SELECT discord_id, player_name, wins, losses, ties"
        " FROM matchplay_records"
        " WHERE format = ?"
        " ORDER BY wins DESC, losses ASC, ties DESC, player_name ASC",
        (format,),
    )
    records = []
    for r in rows:
        records.append({
            "discord_id": r["discord_id"],
            "player_name": r["player_name"],
            "wins": r["wins"],
            "losses": r["losses"],
            "ties": r["ties"],
        })
    return records

// ignore_for_file: dangling_library_doc_comments
/// Shared data models for the Alpenglow VGC companion app.
///
/// Field names mirror the FastAPI backend's JSON keys. Nullable where the
/// backend may omit them.

/// Display rule (Cayden): everywhere the app shows a player to other
/// people, show ONLY their Golf+ username. Falls back to the display name
/// when no handle is set. Pure Dart so models can use it too.
String golferDisplayName(String displayName, String? golfplusHandle) {
  final handle = (golfplusHandle ?? '').trim();
  return handle.isNotEmpty ? handle : displayName;
}

/// One rostered player with an optional Golf+ handle, as sent by the API's
/// app-only enrichment (api/main.py `_player_with_handle`).
class RosterMember {
  final String? discordId;
  final String displayName;
  final String? golfplusHandle;

  const RosterMember({this.discordId, required this.displayName, this.golfplusHandle});

  factory RosterMember.fromJson(Map<String, dynamic> j) => RosterMember(
        discordId: j['discord_id']?.toString(),
        displayName: (j['display_name'] ?? j['discord_id'] ?? '?').toString(),
        golfplusHandle: j['golfplus_handle']?.toString(),
      );

  String get handleName => golferDisplayName(displayName, golfplusHandle);
}

/// Parse hole scores/pars from the API: either a JSON list of ints or a
/// comma-separated string. Tolerant of whitespace and missing values.
List<int> _csvInts(dynamic v) {
  if (v == null) return const [];
  if (v is List) {
    return v
        .map((e) => e is int ? e : int.tryParse(e.toString().trim()))
        .whereType<int>()
        .toList();
  }
  return v
      .toString()
      .split(',')
      .map((s) => int.tryParse(s.trim()))
      .whereType<int>()
      .toList();
}

/// One round of a multi-round tournament: same course as the tournament,
/// with its own tee/pin/wind settings. Green speed is tournament-wide.
class TournamentRound {
  final int roundNumber;
  final String? teePosition;
  final String? pinPosition;
  final String? windStrength;
  final String? startDate;
  final String? endDate;

  TournamentRound({
    required this.roundNumber,
    this.teePosition,
    this.pinPosition,
    this.windStrength,
    this.startDate,
    this.endDate,
  });

  factory TournamentRound.fromJson(Map<String, dynamic> j) => TournamentRound(
        roundNumber: (j['round_number'] as num?)?.toInt() ?? 1,
        teePosition: j['tee_position']?.toString(),
        pinPosition: j['pin_position']?.toString(),
        windStrength: j['wind_strength']?.toString(),
        startDate: j['start_date']?.toString(),
        endDate: j['end_date']?.toString(),
      );

  /// True when the round's window has opened (or no window is set).
  bool get hasStarted {
    if (startDate == null || startDate!.isEmpty) return true;
    final today = DateTime.now();
    final todayKey =
        '${today.year.toString().padLeft(4, '0')}-${today.month.toString().padLeft(2, '0')}-${today.day.toString().padLeft(2, '0')}';
    return todayKey.compareTo(startDate!) >= 0;
  }

  /// True when the round's window has closed (hard cutoff for non-crew).
  bool get hasEnded {
    if (endDate == null || endDate!.isEmpty) return false;
    final today = DateTime.now();
    final todayKey =
        '${today.year.toString().padLeft(4, '0')}-${today.month.toString().padLeft(2, '0')}-${today.day.toString().padLeft(2, '0')}';
    return todayKey.compareTo(endDate!) > 0;
  }

  String get settingsSummary {
    final parts = <String>[];
    if (teePosition != null) parts.add('${_cap(teePosition!)} tees');
    if (pinPosition != null) parts.add('${_cap(pinPosition!)} pins');
    if (windStrength != null) parts.add('${_cap(windStrength!)} wind');
    return parts.join(' · ');
  }

  String get datesSummary {
    if (startDate == null && endDate == null) return '';
    if (startDate != null && startDate == endDate) return _fmtDate(startDate!);
    return '${startDate != null ? _fmtDate(startDate!) : '…'}'
        ' – ${endDate != null ? _fmtDate(endDate!) : '…'}';
  }

  static String _fmtDate(String iso) {
    final d = DateTime.tryParse(iso);
    if (d == null) return iso;
    const months = [
      'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
      'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'
    ];
    return '${months[d.month - 1]} ${d.day}';
  }

  static String _cap(String s) =>
      s.isEmpty ? s : s[0].toUpperCase() + s.substring(1);
}

class Tournament {
  final String id;
  final String name;
  final String? format;
  final int? holes;
  final String? course;
  final String status;
  final String? startDate;
  final String? endDate;
  final String? teePosition;
  final String? pinPosition;
  final String? windStrength;
  final String? greenSpeed;
  final bool registered;
  final List<int>? pars;
  final int numRounds;
  final List<TournamentRound> rounds;

  Tournament({
    required this.id,
    required this.name,
    this.format,
    this.holes,
    this.course,
    required this.status,
    this.startDate,
    this.endDate,
    this.teePosition,
    this.pinPosition,
    this.windStrength,
    this.greenSpeed,
    required this.registered,
    this.pars,
    this.numRounds = 1,
    this.rounds = const [],
  });

  factory Tournament.fromJson(Map<String, dynamic> j) => Tournament(
        id: j['id'].toString(),
        name: (j['name'] ?? 'Tournament').toString(),
        format: j['format']?.toString(),
        holes: (j['holes'] as num?)?.toInt(),
        course: j['course']?.toString(),
        status: (j['status'] ?? 'unknown').toString(),
        startDate: j['start_date']?.toString(),
        endDate: j['end_date']?.toString(),
        teePosition: j['tee_position']?.toString(),
        pinPosition: j['pin_position']?.toString(),
        windStrength: j['wind_strength']?.toString(),
        greenSpeed: j['green_speed']?.toString(),
        registered: j['registered'] == true,
        pars: (j['pars'] as List?)
            ?.map((e) => (e as num).toInt())
            .toList(growable: false),
        numRounds: (j['num_rounds'] as num?)?.toInt() ?? 1,
        rounds: ((j['rounds'] as List?) ?? [])
            .map((e) => TournamentRound.fromJson(e as Map<String, dynamic>))
            .toList(growable: false),
      );

  /// True when the tournament has more than one round configured.
  bool get isMultiRound => numRounds > 1 || rounds.length > 1;

  String get settingsSummary {
    final parts = <String>[];
    if (teePosition != null) parts.add('Tees: $teePosition');
    if (pinPosition != null) parts.add('Pins: $pinPosition');
    if (windStrength != null) parts.add('Wind: $windStrength');
    if (greenSpeed != null) parts.add('Greens: $greenSpeed');
    return parts.join(' · ');
  }
}

class TeeTimePlayer {
  final String discordId;
  final String displayName;
  final String? golfplusHandle;

  TeeTimePlayer({
    required this.discordId,
    required this.displayName,
    this.golfplusHandle,
  });

  factory TeeTimePlayer.fromJson(Map<String, dynamic> j) => TeeTimePlayer(
        discordId: j['discord_id'].toString(),
        displayName: (j['display_name'] ?? j['discord_id']).toString(),
        golfplusHandle: j['golfplus_handle']?.toString(),
      );
}

class TeeTime {
  final String id;
  final String label;
  final DateTime startsAtUtc;
  final int maxPlayers;
  final String createdBy;
  final int roundNumber;
  final List<TeeTimePlayer> players;
  final DateTime? startedAtUtc;
  final DateTime? archivedAtUtc;

  TeeTime({
    required this.id,
    required this.label,
    required this.startsAtUtc,
    required this.maxPlayers,
    required this.createdBy,
    this.roundNumber = 1,
    required this.players,
    this.startedAtUtc,
    this.archivedAtUtc,
  });

  static DateTime? _parseOpt(dynamic v) {
    if (v == null) return null;
    try {
      return DateTime.parse(v.toString()).toUtc();
    } catch (_) {
      return null;
    }
  }

  factory TeeTime.fromJson(Map<String, dynamic> j) {
    DateTime parsed;
    try {
      parsed = DateTime.parse(j['starts_at'].toString()).toUtc();
    } catch (_) {
      parsed = DateTime.now().toUtc();
    }
    return TeeTime(
      id: j['id'].toString(),
      label: (j['label'] ?? 'Tee time').toString(),
      startsAtUtc: parsed,
      maxPlayers: (j['max_players'] as num?)?.toInt() ?? 4,
      createdBy: j['created_by'].toString(),
      roundNumber: (j['round_number'] as num?)?.toInt() ?? 1,
      players: ((j['players'] as List?) ?? [])
          .map((e) => TeeTimePlayer.fromJson(e as Map<String, dynamic>))
          .toList(),
      startedAtUtc: _parseOpt(j['started_at']),
      archivedAtUtc: _parseOpt(j['archived_at']),
    );
  }

  DateTime get startsAtLocal => startsAtUtc.toLocal();
  DateTime? get startedAtLocal => startedAtUtc?.toLocal();
  int get spotsFilled => players.length;
  bool get isFull => spotsFilled >= maxPlayers;
  bool get isStarted => startedAtUtc != null;
  bool get isArchived => archivedAtUtc != null;
  bool hasPassed(DateTime now) => !startsAtUtc.isAfter(now);
}

class TeeTimeRequest {
  final String id;
  final String playerDiscordId;
  final String displayName;
  final String? golfplusHandle;
  final String status;

  TeeTimeRequest({
    required this.id,
    required this.playerDiscordId,
    required this.displayName,
    this.golfplusHandle,
    required this.status,
  });

  factory TeeTimeRequest.fromJson(Map<String, dynamic> j) => TeeTimeRequest(
        id: j['id'].toString(),
        playerDiscordId: j['player_discord_id'].toString(),
        displayName: (j['display_name'] ?? j['player_discord_id']).toString(),
        golfplusHandle: j['golfplus_handle']?.toString(),
        status: (j['status'] ?? 'pending').toString(),
      );

  String get handleName => golferDisplayName(displayName, golfplusHandle);
}

class Scorecard {
  final int? id;
  final String playerDiscordId;
  final List<int?> scores;
  final int? total;
  final int? toPar;
  final int? thru;
  final String? status;
  final String? submittedBy;
  final int roundNumber;
  final String? witnessName;
  /// Per-hole entry timestamps (ISO-8601 UTC), parallel to [scores].
  /// Null entries = hole scored before time tracking existed.
  final List<String?> holeTimes;

  Scorecard({
    this.id,
    required this.playerDiscordId,
    required this.scores,
    this.total,
    this.toPar,
    this.thru,
    this.status,
    this.submittedBy,
    this.roundNumber = 1,
    this.witnessName,
    this.holeTimes = const [],
  });

  /// True while the card is still being entered hole by hole.
  bool get isLive => status == 'in_progress';

  factory Scorecard.fromJson(Map<String, dynamic> j) => Scorecard(
        id: (j['id'] as num?)?.toInt(),
        playerDiscordId: j['player_discord_id'].toString(),
        scores: ((j['scores'] as List?) ?? [])
            .map((e) => e == null ? null : (e as num).toInt())
            .toList(),
        total: (j['total'] as num?)?.toInt(),
        toPar: (j['to_par'] as num?)?.toInt(),
        thru: (j['thru'] as num?)?.toInt(),
        status: j['status']?.toString(),
        submittedBy: j['submitted_by']?.toString(),
        roundNumber: (j['round_number'] as num?)?.toInt() ?? 1,
        witnessName: j['witness_name']?.toString(),
        holeTimes: ((j['hole_times'] as List?) ?? [])
            .map((e) => e?.toString())
            .toList(),
      );
}

/// One tracked golf shot: a landing spot on the hole schematic plus the lie
/// it came to rest in. [seq] is 1-based and assigned by the server;
/// [holed] may only be true on the final shot of the hole.
class Shot {
  final int? seq;
  final double x;
  final double y;
  final String lie;
  final bool holed;

  /// Putt count recorded on a green shot. Legacy: the tracker used to
  /// record one tap plus a count instead of one point per putt. The app
  /// now records every putt as its own point (putts stays 0); a nonzero
  /// value here only appears on older saved holes, where the shot is
  /// always the hole's last shot and holed is true. 0 means "not a
  /// putt-counted shot".
  final int putts;

  const Shot({
    this.seq,
    required this.x,
    required this.y,
    required this.lie,
    this.holed = false,
    this.putts = 0,
  });

  factory Shot.fromJson(Map<String, dynamic> j) => Shot(
        seq: (j['seq'] as num?)?.toInt(),
        x: (j['x'] as num).toDouble(),
        y: (j['y'] as num).toDouble(),
        lie: (j['lie'] ?? 'rough').toString(),
        holed: j['holed'] == true,
        putts: (j['putts'] as num?)?.toInt() ?? 0,
      );

  /// Payload for PUT /api/scorecards/{id}/holes/{hole}/shots — the server
  /// assigns seq on read.
  Map<String, dynamic> toJson() => {
        'x': x,
        'y': y,
        'lie': lie,
        'holed': holed,
        'putts': putts,
      };

  Shot copyWith({double? x, double? y, String? lie, bool? holed, int? putts}) =>
      Shot(
        seq: seq,
        x: x ?? this.x,
        y: y ?? this.y,
        lie: lie ?? this.lie,
        holed: holed ?? this.holed,
        putts: putts ?? this.putts,
      );

  /// Value equality on the tracked content. `seq` is deliberately excluded:
  /// the server assigns it on read, so two shots differing only in seq are
  /// the same tracked shot. Used for dirty-tracking in the shot tracker.
  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      other is Shot &&
          x == other.x &&
          y == other.y &&
          lie == other.lie &&
          holed == other.holed &&
          putts == other.putts;

  @override
  int get hashCode => Object.hash(x, y, lie, holed, putts);
}

/// Honest shot-tracking stats for one player, from
/// GET /api/players/{key}/stats. Percentages are 0–100 (1 decimal), or null
/// when there is no data behind them; [handicapIndex] is null with fewer
/// than 3 completed rounds.
class PlayerShotStats {
  final String playerDiscordId;
  final String displayName;
  final String? golfplusHandle;
  final int roundsTracked;
  final int roundsFullyTracked;
  final double? fairwaysHitPct;
  final double? girPct;
  final double? puttsPerRound;
  final double? puttsPerGir;
  final double? upDownPct;
  final double? sandSavePct;
  final double? handicapIndex;

  const PlayerShotStats({
    required this.playerDiscordId,
    required this.displayName,
    this.golfplusHandle,
    this.roundsTracked = 0,
    this.roundsFullyTracked = 0,
    this.fairwaysHitPct,
    this.girPct,
    this.puttsPerRound,
    this.puttsPerGir,
    this.upDownPct,
    this.sandSavePct,
    this.handicapIndex,
  });

  static double? _num(Map<String, dynamic> j, String key) {
    final v = j[key];
    return v == null ? null : (v as num).toDouble();
  }

  factory PlayerShotStats.fromJson(Map<String, dynamic> j) {
    final p = (j['player'] as Map<String, dynamic>?) ?? const {};
    return PlayerShotStats(
      playerDiscordId: (p['discord_id'] ?? j['discord_id'] ?? '').toString(),
      displayName:
          (p['display_name'] ?? j['display_name'] ?? '?').toString(),
      golfplusHandle: p['golfplus_handle']?.toString(),
      roundsTracked: (j['rounds_tracked'] as num?)?.toInt() ?? 0,
      roundsFullyTracked: (j['rounds_fully_tracked'] as num?)?.toInt() ?? 0,
      fairwaysHitPct: _num(j, 'fairways_hit_pct'),
      girPct: _num(j, 'gir_pct'),
      puttsPerRound: _num(j, 'putts_per_round'),
      puttsPerGir: _num(j, 'putts_per_gir'),
      upDownPct: _num(j, 'up_down_pct'),
      sandSavePct: _num(j, 'sand_save_pct'),
      handicapIndex: _num(j, 'handicap_index'),
    );
  }

  /// Handle-first display rule for showing this player to others.
  String get handleName => golferDisplayName(displayName, golfplusHandle);

  /// True when the player has tracked at least one hole of shot data.
  bool get hasAnyData =>
      roundsTracked > 0 ||
      fairwaysHitPct != null ||
      girPct != null ||
      handicapIndex != null;
}

/// One completed/submitted round in a player's round history —
/// either a tournament round or a casual round. Timestamps come from the
/// tee time; pars may be null when the course's pars are unknown.
class RoundSummary {
  final int scorecardId;
  final String kind; // 'tournament' | 'casual'
  final int? tournamentId;
  final String? tournamentName;
  final int roundNumber;
  final String? label; // casual tee-time label
  final String course;
  final String? format; // casual format, e.g. 'stroke'
  final String? startsAt; // tee-time timestamp (ISO)
  final String? submittedAt;
  final String status;
  final String? witnessName;
  final int holes;
  final List<int?> scores;
  final List<int>? pars;
  final int total;
  final int? toPar;

  const RoundSummary({
    required this.scorecardId,
    required this.kind,
    this.tournamentId,
    this.tournamentName,
    this.roundNumber = 1,
    this.label,
    required this.course,
    this.format,
    this.startsAt,
    this.submittedAt,
    required this.status,
    this.witnessName,
    required this.holes,
    required this.scores,
    this.pars,
    required this.total,
    this.toPar,
  });

  static List<int?> _scores(dynamic v) {
    if (v is! List) return const [];
    return v.map((s) => (s as num?)?.toInt()).toList();
  }

  static List<int>? _ints(dynamic v) {
    if (v is! List) return null;
    final out = <int>[];
    for (final e in v) {
      final n = (e as num?)?.toInt();
      if (n == null) return null;
      out.add(n);
    }
    return out;
  }

  factory RoundSummary.fromJson(Map<String, dynamic> j) => RoundSummary(
        scorecardId: (j['scorecard_id'] as num).toInt(),
        kind: (j['kind'] ?? 'tournament').toString(),
        tournamentId: (j['tournament_id'] as num?)?.toInt(),
        tournamentName: j['tournament_name']?.toString(),
        roundNumber: (j['round_number'] as num?)?.toInt() ?? 1,
        label: j['label']?.toString(),
        course: (j['course'] ?? '').toString(),
        format: j['format']?.toString(),
        startsAt: j['starts_at']?.toString(),
        submittedAt: j['submitted_at']?.toString(),
        status: (j['status'] ?? '').toString(),
        witnessName: j['witness_name']?.toString(),
        holes: (j['holes'] as num?)?.toInt() ?? 18,
        scores: _scores(j['scores']),
        pars: _ints(j['pars']),
        total: (j['total'] as num?)?.toInt() ?? 0,
        toPar: (j['to_par'] as num?)?.toInt(),
      );

  bool get isTournament => kind == 'tournament';

  /// Display title: tournament name + round, or the casual label/course.
  String get title {
    if (isTournament) {
      final name = tournamentName ?? 'Tournament';
      return roundNumber > 1 ? '$name · Round $roundNumber' : name;
    }
    if (label != null && label!.isNotEmpty) return label!;
    return course.isNotEmpty ? course : 'Casual round';
  }

  /// Subtitle line: kind badge text + format for casual rounds.
  String get kindLabel {
    if (isTournament) return 'Tournament';
    final fmt = format;
    if (fmt != null && fmt.isNotEmpty && fmt != 'stroke') {
      return 'Casual · ${_formatName(fmt)}';
    }
    return 'Casual';
  }

  static String _formatName(String fmt) {
    switch (fmt) {
      case 'best_ball':
        return 'Best ball';
      case 'match_play':
        return 'Match play';
      case 'alt_shot':
        return 'Alt shot';
      default:
        return fmt;
    }
  }

  /// Holes actually played (non-null scores).
  int get thru => scores.where((s) => s != null).length;
}

/// One row of the tournament players list: an account with a Golf+
/// username, flagged registered or not for the tournament.
class TournamentPlayer {
  final String discordId;
  final String displayName;
  final String golfplusHandle;
  final bool registered;
  final String? registeredAt;

  const TournamentPlayer({
    required this.discordId,
    required this.displayName,
    required this.golfplusHandle,
    required this.registered,
    this.registeredAt,
  });

  factory TournamentPlayer.fromJson(Map<String, dynamic> j) =>
      TournamentPlayer(
        discordId: (j['discord_id'] ?? '').toString(),
        displayName: (j['display_name'] ?? '').toString(),
        golfplusHandle: (j['golfplus_handle'] ?? '').toString(),
        registered: j['registered'] == true,
        registeredAt: j['registered_at']?.toString(),
      );
}

class LeaderboardEntry {
  final Map<String, dynamic> raw;

  LeaderboardEntry(this.raw);

  dynamic operator [](String key) => raw[key];

  String get name {
    // Handle-first display rule: Golf+ username only, never real names.
    final handle = (raw['golfplus_handle']?.toString() ?? '').trim();
    if (handle.isNotEmpty) return handle;
    return (raw['display_name'] ?? raw['name'] ?? raw['player'] ?? '?')
        .toString();
  }
  String? get rank => raw['rank']?.toString();
  String? get total => raw['total']?.toString();
  String? get toPar => raw['to_par']?.toString() ?? raw['toPar']?.toString();

  /// Multi-round fields: how many rounds this entry has completed, out of
  /// the tournament's round count, plus the per-round breakdown.
  int? get roundsPlayed => (raw['rounds_played'] as num?)?.toInt();
  int? get numRounds => (raw['num_rounds'] as num?)?.toInt();
  List<Map<String, dynamic>> get roundBreakdown =>
      ((raw['rounds'] as List?) ?? [])
          .whereType<Map<String, dynamic>>()
          .toList(growable: false);

  /// Live scoring: the player/team is mid-round, with this many holes entered.
  bool get onCourse => raw['on_course'] == true;
  int? get thru => (raw['thru'] as num?)?.toInt();

  /// Card status: verified / pending / in_progress (may be absent on
  /// aggregate rows).
  String? get status => raw['status']?.toString();
  bool get isPending => status == 'pending';
}

/// One row of the season points standings
/// (GET /api/seasons/standings -> standings[]).
class SeasonStandingEntry {
  final String discordId;
  final String displayName;
  final String? golfplusHandle;
  final int totalPoints;
  final int tournamentsPlayed;

  const SeasonStandingEntry({
    required this.discordId,
    required this.displayName,
    this.golfplusHandle,
    required this.totalPoints,
    required this.tournamentsPlayed,
  });

  factory SeasonStandingEntry.fromJson(Map<String, dynamic> json) {
    return SeasonStandingEntry(
      discordId: (json['discord_id'] ?? '').toString(),
      displayName:
          (json['display_name'] ?? json['name'] ?? '?').toString(),
      golfplusHandle: json['golfplus_handle']?.toString(),
      totalPoints: (json['total_points'] as num?)?.toInt() ?? 0,
      tournamentsPlayed:
          (json['tournaments_played'] as num?)?.toInt() ?? 0,
    );
  }
}

/// Season points standings: the active season's name plus ranked entries.
/// [SeasonStandings.empty] represents "no active season / no standings".
class SeasonStandings {
  final int? seasonId;
  final String seasonName;
  final String? seasonStartDate; // YYYY-MM-DD or null
  final String? seasonEndDate; // YYYY-MM-DD or null
  final List<SeasonStandingEntry> entries;

  const SeasonStandings({
    this.seasonId,
    required this.seasonName,
    required this.entries,
    this.seasonStartDate,
    this.seasonEndDate,
  });

  const SeasonStandings.empty()
      : seasonId = null,
        seasonName = '',
        seasonStartDate = null,
        seasonEndDate = null,
        entries = const [];
}

class PlayerMe {
  final String discordId;
  final String displayName;
  final String? golfplusHandle;
  final String? timezone;
  final bool isCrew;
  final bool isAdmin;
  final bool canManageScores;
  final bool statsPrivate;

  PlayerMe({
    required this.discordId,
    required this.displayName,
    this.golfplusHandle,
    this.timezone,
    this.isCrew = false,
    this.isAdmin = false,
    this.canManageScores = false,
    this.statsPrivate = false,
  });

  factory PlayerMe.fromJson(Map<String, dynamic> j) => PlayerMe(
        discordId: j['discord_id'].toString(),
        displayName: (j['display_name'] ?? j['discord_id']).toString(),
        golfplusHandle: j['golfplus_handle']?.toString(),
        timezone: j['timezone']?.toString(),
        isCrew: j['is_crew'] == true,
        isAdmin: j['is_admin'] == true,
        canManageScores: j['can_manage_scores'] == true,
        statsPrivate: j['stats_private'] == true,
      );
}

/// A player row for the crew management admin list, from GET /api/admin/players.
class CrewPlayer {
  final String discordId;
  final String displayName;
  final String? golfplusHandle;
  final List<String> roles;
  final bool isLocal;
  final String? email;

  CrewPlayer({
    required this.discordId,
    required this.displayName,
    this.golfplusHandle,
    List<String>? roles,
    this.isLocal = false,
    this.email,
  }) : roles = roles ?? [];

  bool hasRole(String role) =>
      roles.any((r) => r.toLowerCase() == role.toLowerCase());

  factory CrewPlayer.fromJson(Map<String, dynamic> j) {
    final id = j['discord_id'].toString();
    return CrewPlayer(
      discordId: id,
      displayName: (j['display_name'] ?? j['discord_id']).toString(),
      golfplusHandle: j['golfplus_handle']?.toString(),
      roles: (j['roles'] as List? ?? [])
          .map((r) => r.toString())
          .toList(growable: false),
      isLocal: (j['is_local'] == true) || id.startsWith('local:'),
      email: j['email']?.toString(),
    );
  }
}

/// A Golf+ course with official hole-by-hole pars, from /api/courses.
class GolfCourse {
  final String name;
  final List<int> pars;

  GolfCourse({required this.name, required this.pars});

  int get parTotal => pars.fold(0, (a, b) => a + b);

  factory GolfCourse.fromJson(Map<String, dynamic> j) => GolfCourse(
        name: j['name'].toString(),
        pars: ((j['pars'] as List?) ?? [])
            .map((e) => (e as num).toInt())
            .toList(growable: false),
      );
}

/// A casual (non-tournament) tee time. Anyone can create one; anyone can
/// join any number of them — no one-round-per-player rule here.
class CasualTeeTime {
  final String id;
  final String creatorDiscordId;
  final String label;
  final String course;
  final String teePosition;
  final String pinPosition;
  final String windStrength;
  final String greenSpeed;
  final String startsAt;
  final int maxPlayers;
  final String notes;
  final String format; // stroke | best_ball | match_play | alt_shot
  final String? matchplayTeeTimeId;
  final String? altshotTeeTimeId;
  final List<int>? pars;
  final List<CasualPlayer> players;

  CasualTeeTime({
    required this.id,
    required this.creatorDiscordId,
    required this.label,
    required this.course,
    required this.teePosition,
    required this.pinPosition,
    required this.windStrength,
    required this.greenSpeed,
    required this.startsAt,
    required this.maxPlayers,
    required this.notes,
    this.format = 'stroke',
    this.matchplayTeeTimeId,
    this.altshotTeeTimeId,
    this.pars,
    required this.players,
  });

  factory CasualTeeTime.fromJson(Map<String, dynamic> j) => CasualTeeTime(
        id: j['id'].toString(),
        creatorDiscordId: (j['creator_discord_id'] ?? '').toString(),
        label: (j['label'] ?? '').toString(),
        course: (j['course'] ?? '').toString(),
        teePosition: (j['tee_position'] ?? 'middle').toString(),
        pinPosition: (j['pin_position'] ?? 'white').toString(),
        windStrength: (j['wind_strength'] ?? 'moderate').toString(),
        greenSpeed: (j['green_speed'] ?? 'pro').toString(),
        startsAt: (j['starts_at'] ?? '').toString(),
        maxPlayers: (j['max_players'] as num?)?.toInt() ?? 4,
        notes: (j['notes'] ?? '').toString(),
        format: (j['format'] ?? 'stroke').toString(),
        matchplayTeeTimeId: j['matchplay_tee_time_id']?.toString(),
        altshotTeeTimeId: j['altshot_tee_time_id']?.toString(),
        pars: (j['pars'] as String?)
            ?.split(',')
            .map((e) => int.tryParse(e.trim()))
            .whereType<int>()
            .toList(),
        players: ((j['players'] as List?) ?? [])
            .map((e) => CasualPlayer.fromJson(e as Map<String, dynamic>))
            .toList(),
      );

  bool isIn(String discordId) =>
      players.any((p) => p.discordId == discordId);

  bool get isFull => players.length >= maxPlayers;

  bool get isMatchPlay => format == 'match_play';
  bool get isAltShot => format == 'alt_shot';
  bool get usesScorecards => format == 'stroke' || format == 'best_ball';

  String get formatLabel {
    switch (format) {
      case 'best_ball':
        return 'Best Ball';
      case 'match_play':
        return 'Match Play';
      case 'alt_shot':
        return 'Alt-Shot';
      default:
        return 'Stroke';
    }
  }

  String get settingsSummary {
    final parts = <String>[
      '${_capWord(teePosition)} tees',
      '${_capWord(pinPosition)} pins',
      '${_capWord(windStrength)} wind',
      greenSpeed == 'veryfast' ? 'Very fast greens' : 'Pro greens',
    ];
    return parts.join(' · ');
  }
}

class CasualPlayer {
  final String discordId;
  final String displayName;
  final String? golfplusHandle;

  CasualPlayer(
      {required this.discordId, required this.displayName, this.golfplusHandle});

  factory CasualPlayer.fromJson(Map<String, dynamic> j) => CasualPlayer(
        discordId: j['discord_id'].toString(),
        displayName: (j['display_name'] ?? j['discord_id']).toString(),
        golfplusHandle: j['golfplus_handle']?.toString(),
      );

  String get handleName => golferDisplayName(displayName, golfplusHandle);
}

String _capWord(String s) =>
    s.isEmpty ? s : s[0].toUpperCase() + s.substring(1);

/// Match-play: 2-sided tee times, single (1v1) or best-ball (2-4 per side).
/// Scoring is hole-by-hole: +1 = side 1 wins the hole, -1 = side 2 wins,
/// 0 = halved, null = unplayed. The server computes lead/played/remaining
/// and the result text ("3&2", "Dormie", "All Square", "2 UP").
class MatchPlayScore {
  final List<int?> holeResults;
  final int lead;
  final int played;
  final int remaining;
  final String status; // in_progress | completed
  final int? winnerSide;
  final String? resultText;
  final String liveText;
  final String submittedBy;
  final String submittedAt;

  MatchPlayScore({
    required this.holeResults,
    required this.lead,
    required this.played,
    required this.remaining,
    required this.status,
    this.winnerSide,
    this.resultText,
    required this.liveText,
    required this.submittedBy,
    required this.submittedAt,
  });

  factory MatchPlayScore.fromJson(Map<String, dynamic> j) =>
      MatchPlayScore(
        holeResults: ((j['hole_results'] as List?) ?? [])
            .map((e) => e == null ? null : (e as num).toInt())
            .toList(),
        lead: (j['lead'] as num?)?.toInt() ?? 0,
        played: (j['played'] as num?)?.toInt() ?? 0,
        remaining: (j['remaining'] as num?)?.toInt() ?? 18,
        status: (j['status'] ?? 'in_progress').toString(),
        winnerSide: (j['winner_side'] as num?)?.toInt(),
        resultText: j['result_text']?.toString(),
        liveText: (j['live_text'] ?? '').toString(),
        submittedBy: (j['submitted_by'] ?? '').toString(),
        submittedAt: (j['submitted_at'] ?? '').toString(),
      );

  bool get isCompleted => status == 'completed';

  String get thruLine => played == 0 ? '' : 'thru $played';
}

class MatchPlaySide {
  final String id;
  final int sideNumber;
  final List<String> memberDiscordIds;
  final List<String> memberNames;
  final List<RosterMember> members;
  final int size;
  final String displayName;

  MatchPlaySide({
    required this.id,
    required this.sideNumber,
    required this.memberDiscordIds,
    required this.memberNames,
    required this.members,
    required this.size,
    required this.displayName,
  });

  factory MatchPlaySide.fromJson(Map<String, dynamic> j) => MatchPlaySide(
        id: j['id'].toString(),
        sideNumber: (j['side_number'] as num?)?.toInt() ?? 1,
        memberDiscordIds: ((j['member_discord_ids'] as List?) ?? [])
            .map((e) => e.toString())
            .toList(),
        memberNames: ((j['member_names'] as List?) ?? [])
            .map((e) => e.toString())
            .toList(),
        members: ((j['members'] as List?) ?? [])
            .whereType<Map<String, dynamic>>()
            .map(RosterMember.fromJson)
            .toList(),
        size: (j['size'] as num?)?.toInt() ?? 0,
        displayName: (j['display_name'] ?? '').toString(),
      );

  bool isMember(String discordId) => memberDiscordIds.contains(discordId);

  /// Side label recomposed from Golf+ handles (handle-first display rule).
  /// Falls back to the server-composed label when structured members are
  /// absent (older servers).
  String get handleLabel => members.isEmpty
      ? displayName
      : members.map((m) => m.handleName).join(' & ');
}

class MatchPlayTeeTime {
  final String id;
  final String creatorDiscordId;
  final String label;
  final String course;
  final List<int> pars;
  final String teePosition;
  final String pinPosition;
  final String windStrength;
  final String greenSpeed;
  final String startsAt;
  final String format; // single | bestball
  final int teamSize;
  final int sideCap;
  final String notes;
  final String createdAt;
  final List<MatchPlaySide> sides;
  final bool bothFull;
  final MatchPlayScore? score;

  MatchPlayTeeTime({
    required this.id,
    required this.creatorDiscordId,
    required this.label,
    required this.course,
    required this.pars,
    required this.teePosition,
    required this.pinPosition,
    required this.windStrength,
    required this.greenSpeed,
    required this.startsAt,
    required this.format,
    required this.teamSize,
    required this.sideCap,
    required this.notes,
    required this.createdAt,
    required this.sides,
    required this.bothFull,
    this.score,
  });

  factory MatchPlayTeeTime.fromJson(Map<String, dynamic> j) =>
      MatchPlayTeeTime(
        id: j['id'].toString(),
        creatorDiscordId: (j['creator_discord_id'] ?? '').toString(),
        label: (j['label'] ?? '').toString(),
        course: (j['course'] ?? '').toString(),
        pars: ((j['pars'] ?? '').toString().split(','))
            .map((e) => int.tryParse(e.trim()) ?? 4)
            .toList(),
        teePosition: (j['tee_position'] ?? 'back').toString(),
        pinPosition: (j['pin_position'] ?? 'black').toString(),
        windStrength: (j['wind_strength'] ?? 'moderate').toString(),
        greenSpeed: (j['green_speed'] ?? 'pro').toString(),
        startsAt: (j['starts_at'] ?? '').toString(),
        format: (j['format'] ?? 'single').toString(),
        teamSize: (j['team_size'] as num?)?.toInt() ?? 1,
        sideCap: (j['side_cap'] as num?)?.toInt() ?? 1,
        notes: (j['notes'] ?? '').toString(),
        createdAt: (j['created_at'] ?? '').toString(),
        sides: ((j['sides'] as List?) ?? [])
            .map((e) => MatchPlaySide.fromJson(e as Map<String, dynamic>))
            .toList(),
        bothFull: j['both_full'] == true,
        score: j['score'] == null
            ? null
            : MatchPlayScore.fromJson(j['score'] as Map<String, dynamic>),
      );

  bool get isSingle => format == 'single';

  MatchPlaySide? mySide(String discordId) {
    for (final s in sides) {
      if (s.isMember(discordId)) return s;
    }
    return null;
  }

  bool get canScore => bothFull;

  String get settingsSummary {
    final parts = <String>[
      '${_capWord(teePosition)} tees',
      '${_capWord(pinPosition)} pins',
      '${_capWord(windStrength)} wind',
      greenSpeed == 'veryfast' ? 'Very fast greens' : 'Pro greens',
    ];
    return parts.join(' · ');
  }

  String get formatSummary =>
      isSingle ? 'Single match play' : 'Best-ball · $teamSize per side';
}

/// Per-player match-play record: wins, losses, ties for one course + setup.
class MatchPlayRecord {
  final String discordId;
  final String playerName;
  final String? golfplusHandle;
  final int wins;
  final int losses;
  final int ties;

  MatchPlayRecord({
    required this.discordId,
    required this.playerName,
    this.golfplusHandle,
    required this.wins,
    required this.losses,
    required this.ties,
  });

  factory MatchPlayRecord.fromJson(Map<String, dynamic> j) =>
      MatchPlayRecord(
        discordId: j['discord_id'].toString(),
        playerName: (j['player_name'] ?? j['discord_id']).toString(),
        golfplusHandle: j['golfplus_handle']?.toString(),
        wins: (j['wins'] as num?)?.toInt() ?? 0,
        losses: (j['losses'] as num?)?.toInt() ?? 0,
        ties: (j['ties'] as num?)?.toInt() ?? 0,
      );

  String get handleName => golferDisplayName(playerName, golfplusHandle);

  String get recordLine => '$wins–$losses–$ties';

  int get played => wins + losses + ties;

  double get winPct => played == 0 ? 0.0 : wins / played;

  String get winPctLine => '${(winPct * 100).toStringAsFixed(1)}%';
}

/// each team is one registered player plus an optional partner name. Scores
/// are submitted once per team (not live); a team needs both players attached
/// to submit to the per-course record leaderboard.
class AltShotScore {
  final List<int> holes;
  final int total;
  final String submittedAt;

  AltShotScore(
      {required this.holes, required this.total, required this.submittedAt});

  factory AltShotScore.fromJson(Map<String, dynamic> j) => AltShotScore(
        holes: ((j['holes'] as List?) ?? [])
            .map((e) => (e as num).toInt())
            .toList(),
        total: (j['total'] as num).toInt(),
        submittedAt: (j['submitted_at'] ?? '').toString(),
      );
}

class AltShotTeam {
  final String id;
  final String player1DiscordId;
  final String player1Name;
  final List<String> playerNames;
  final List<String> memberDiscordIds;
  final List<RosterMember> members;
  final int teamSize;
  final String displayName;
  final int teamNumber;
  final AltShotScore? score;

  AltShotTeam({
    required this.id,
    required this.player1DiscordId,
    required this.player1Name,
    required this.playerNames,
    required this.memberDiscordIds,
    required this.members,
    required this.teamSize,
    required this.displayName,
    this.teamNumber = 1,
    this.score,
  });

  factory AltShotTeam.fromJson(Map<String, dynamic> j) => AltShotTeam(
        id: j['id'].toString(),
        player1DiscordId: (j['player1_discord_id'] ?? '').toString(),
        player1Name: (j['player1_name'] ?? '').toString(),
        playerNames: ((j['player_names'] as List?) ?? [])
            .map((e) => e.toString())
            .toList(),
        memberDiscordIds: ((j['member_discord_ids'] as List?) ?? [])
            .map((e) => e.toString())
            .toList(),
        members: ((j['members'] as List?) ?? [])
            .whereType<Map<String, dynamic>>()
            .map(RosterMember.fromJson)
            .toList(),
        teamSize: (j['team_size'] as num?)?.toInt() ?? 1,
        displayName: (j['display_name'] ?? '').toString(),
        teamNumber: (j['team_number'] as num?)?.toInt() ?? 1,
        score: j['score'] == null
            ? null
            : AltShotScore.fromJson(j['score'] as Map<String, dynamic>),
      );

  /// A team needs at least 2 players to submit a score to the records.
  bool get canSubmit => teamSize >= 2;

  bool isMember(String discordId) => memberDiscordIds.contains(discordId);

  /// Display names with the handle-first rule: registered members show
  /// their Golf+ username; typed-in extras (1-team legacy) show as-is.
  /// The first memberDiscordIds.length entries of playerNames correspond
  /// to members in order.
  List<String> get handleNames {
    final out = <String>[];
    for (var i = 0; i < playerNames.length; i++) {
      out.add(i < members.length ? members[i].handleName : playerNames[i]);
    }
    return out;
  }

  /// Subtitle line: every player on the team.
  String get playersLine => handleNames.join(' · ');
}

class AltShotTeeTime {
  final String id;
  final String creatorDiscordId;
  final String label;
  final String course;
  final List<int> pars;
  final String teePosition;
  final String pinPosition;
  final String windStrength;
  final String greenSpeed;
  final String startsAt;
  final int maxTeams;
  final int? teamSize;
  final String notes;
  final List<AltShotTeam> teams;

  AltShotTeeTime({
    required this.id,
    required this.creatorDiscordId,
    required this.label,
    required this.course,
    required this.pars,
    required this.teePosition,
    required this.pinPosition,
    required this.windStrength,
    required this.greenSpeed,
    required this.startsAt,
    required this.maxTeams,
    this.teamSize,
    required this.notes,
    required this.teams,
  });

  factory AltShotTeeTime.fromJson(Map<String, dynamic> j) => AltShotTeeTime(
        id: j['id'].toString(),
        creatorDiscordId: (j['creator_discord_id'] ?? '').toString(),
        label: (j['label'] ?? '').toString(),
        course: (j['course'] ?? '').toString(),
        pars: ((j['pars'] ?? '').toString().split(','))
            .map((e) => int.tryParse(e.trim()) ?? 4)
            .toList(),
        teePosition: (j['tee_position'] ?? 'middle').toString(),
        pinPosition: (j['pin_position'] ?? 'white').toString(),
        windStrength: (j['wind_strength'] ?? 'moderate').toString(),
        greenSpeed: (j['green_speed'] ?? 'pro').toString(),
        startsAt: (j['starts_at'] ?? '').toString(),
        maxTeams: (j['max_teams'] as num?)?.toInt() ?? 2,
        teamSize: (j['team_size'] as num?)?.toInt(),
        notes: (j['notes'] ?? '').toString(),
        teams: ((j['teams'] as List?) ?? [])
            .map((e) => AltShotTeam.fromJson(e as Map<String, dynamic>))
            .toList(),
      );

  bool get isFull {
    if (isOneTeam) {
      return teams.isNotEmpty && teams.first.teamSize >= (teamSize ?? 99);
    }
    if (isFixedTwoTeam) {
      return teams.length == 2 &&
          teams.every((t) => t.teamSize >= (teamSize ?? 99));
    }
    return teams.length >= maxTeams;
  }

  /// 1-team fixed-roster mode: the single team has a set roster size.
  bool get isOneTeam => maxTeams == 1 && teamSize != null;

  /// Fixed 2-team mode: two pre-created teams with the same roster size;
  /// registered players only, creator starts on Team 1.
  bool get isFixedTwoTeam => maxTeams == 2 && teamSize != null;

  /// Legacy 2-team tee times created before team_size became mandatory:
  /// joining creates your own team.
  bool get isLegacyFlexible => maxTeams == 2 && teamSize == null;

  /// Old name for 1-team mode, kept for existing call sites.
  bool get isFixedRoster => isOneTeam;

  /// Whether [team]'s score may be submitted: 1-team mode needs the single
  /// roster full; fixed 2-team mode needs BOTH teams full; legacy flexible
  /// teams need at least 2 players.
  bool canSubmitScore(AltShotTeam team) {
    if (isOneTeam) return team.teamSize == teamSize;
    if (isFixedTwoTeam) {
      return teams.length == 2 &&
          teams.every((t) => t.teamSize == teamSize);
    }
    return team.canSubmit;
  }

  /// Any submitted score in this tee time (locks team membership).
  bool get anyScoreSubmitted => teams.any((t) => t.score != null);

  AltShotTeam? teamByNumber(int n) {
    for (final t in teams) {
      if (t.teamNumber == n) return t;
    }
    return null;
  }

  AltShotTeam? myTeam(String discordId) {
    for (final t in teams) {
      if (t.memberDiscordIds.contains(discordId)) return t;
    }
    return null;
  }

  String get settingsSummary {
    final parts = <String>[
      '${_capWord(teePosition)} tees',
      '${_capWord(pinPosition)} pins',
      '${_capWord(windStrength)} wind',
      greenSpeed == 'veryfast' ? 'Very fast greens' : 'Pro greens',
    ];
    return parts.join(' · ');
  }
}

class AltShotRecord {
  final String teamDisplay;
  final List<String> playerNames;
  final List<RosterMember> players;
  final int teamSize;
  final int total;
  final int? toPar;
  final List<int> holes;
  final List<int> pars;
  final String teeTimeLabel;
  final String teePosition;
  final String pinPosition;
  final String windStrength;
  final String greenSpeed;
  final String submittedAt;

  AltShotRecord({
    required this.teamDisplay,
    required this.playerNames,
    required this.players,
    required this.teamSize,
    required this.total,
    this.toPar,
    required this.holes,
    required this.pars,
    required this.teeTimeLabel,
    required this.teePosition,
    required this.pinPosition,
    required this.windStrength,
    required this.greenSpeed,
    required this.submittedAt,
  });

  factory AltShotRecord.fromJson(Map<String, dynamic> j) => AltShotRecord(
        teamDisplay: (j['team_display'] ?? '').toString(),
        playerNames: ((j['player_names'] as List?) ?? [])
            .map((e) => e.toString())
            .toList(),
        players: ((j['players'] as List?) ?? [])
            .whereType<Map<String, dynamic>>()
            .map(RosterMember.fromJson)
            .toList(),
        teamSize: (j['team_size'] as num?)?.toInt() ?? 2,
        total: (j['total'] as num).toInt(),
        toPar: (j['to_par'] as num?)?.toInt(),
        holes: _csvInts(j['holes']),
        pars: _csvInts(j['pars']),
        teeTimeLabel: (j['tee_time_label'] ?? '').toString(),
        teePosition: (j['tee_position'] ?? '').toString(),
        pinPosition: (j['pin_position'] ?? '').toString(),
        windStrength: (j['wind_strength'] ?? '').toString(),
        greenSpeed: (j['green_speed'] ?? '').toString(),
        submittedAt: (j['submitted_at'] ?? '').toString(),
      );

  /// Subtitle: every player on the team, Golf+ usernames first.
  String get playersLine => (players.isNotEmpty
          ? players.map((p) => p.handleName)
          : playerNames)
      .join(' · ');

  /// Title: roster recomposed from Golf+ usernames (' & ' separated,
  /// matching the old teamDisplay shape).
  String get handleTitle => (players.isNotEmpty
          ? players.map((p) => p.handleName)
          : playerNames)
      .join(' & ');

  String get settingsSummary {
    final parts = <String>[
      '${_capWord(teePosition)} tees',
      '${_capWord(pinPosition)} pins',
      '${_capWord(windStrength)} wind',
      greenSpeed == 'veryfast' ? 'Very fast greens' : 'Pro greens',
    ];
    return parts.join(' · ');
  }

  String get scoreLine {
    if (toPar == null) return '$total';
    if (toPar == 0) return '$total (E)';
    return '$total (${toPar! > 0 ? '+' : ''}$toPar)';
  }
}

/// Best submitted alt-shot record for one course, from the batched
/// /api/altshot-records/summary lookup used by the tee-time course picker.
class AltShotCourseRecord {
  final String course;
  final int total;
  final int? toPar;
  final String teamDisplay;
  final List<RosterMember> players;

  AltShotCourseRecord({
    required this.course,
    required this.total,
    this.toPar,
    required this.teamDisplay,
    required this.players,
  });

  factory AltShotCourseRecord.fromJson(Map<String, dynamic> j) =>
      AltShotCourseRecord(
        course: (j['course'] ?? '').toString(),
        total: (j['total'] as num).toInt(),
        toPar: (j['to_par'] as num?)?.toInt(),
        teamDisplay: (j['team_display'] ?? '').toString(),
        players: ((j['players'] as List?) ?? [])
            .whereType<Map<String, dynamic>>()
            .map(RosterMember.fromJson)
            .toList(),
      );

  /// Record holders recomposed from Golf+ usernames.
  String get holderLine => players.isNotEmpty
      ? players.map((p) => p.handleName).join(' & ')
      : teamDisplay;

  /// "68 (-4) · Aces" — same score formatting as [AltShotRecord.scoreLine].
  String get recordLine {
    final score = toPar == null
        ? '$total'
        : (toPar == 0
            ? '$total (E)'
            : '$total (${toPar! > 0 ? '+' : ''}$toPar)');
    return '$score · $holderLine';
  }
}

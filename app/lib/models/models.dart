// ignore_for_file: dangling_library_doc_comments
/// Shared data models for the Alpenglow VGC companion app.
///
/// Field names mirror the FastAPI backend's JSON keys. Nullable where the
/// backend may omit them.

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

  TeeTime({
    required this.id,
    required this.label,
    required this.startsAtUtc,
    required this.maxPlayers,
    required this.createdBy,
    this.roundNumber = 1,
    required this.players,
  });

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
    );
  }

  DateTime get startsAtLocal => startsAtUtc.toLocal();
  int get spotsFilled => players.length;
  bool get isFull => spotsFilled >= maxPlayers;
  bool hasPassed(DateTime now) => !startsAtUtc.isAfter(now);
}

class TeeTimeRequest {
  final String id;
  final String playerDiscordId;
  final String displayName;
  final String status;

  TeeTimeRequest({
    required this.id,
    required this.playerDiscordId,
    required this.displayName,
    required this.status,
  });

  factory TeeTimeRequest.fromJson(Map<String, dynamic> j) => TeeTimeRequest(
        id: j['id'].toString(),
        playerDiscordId: j['player_discord_id'].toString(),
        displayName: (j['display_name'] ?? j['player_discord_id']).toString(),
        status: (j['status'] ?? 'pending').toString(),
      );
}

class Scorecard {
  final String playerDiscordId;
  final List<int?> scores;
  final int? total;
  final int? toPar;
  final int? thru;
  final String? status;
  final String? submittedBy;
  final int roundNumber;
  final String? witnessName;

  Scorecard({
    required this.playerDiscordId,
    required this.scores,
    this.total,
    this.toPar,
    this.thru,
    this.status,
    this.submittedBy,
    this.roundNumber = 1,
    this.witnessName,
  });

  /// True while the card is still being entered hole by hole.
  bool get isLive => status == 'in_progress';

  factory Scorecard.fromJson(Map<String, dynamic> j) => Scorecard(
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
      );
}

class LeaderboardEntry {
  final Map<String, dynamic> raw;

  LeaderboardEntry(this.raw);

  dynamic operator [](String key) => raw[key];

  String get name =>
      (raw['display_name'] ?? raw['name'] ?? raw['player'] ?? '?').toString();
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

class PlayerMe {
  final String discordId;
  final String displayName;
  final String? golfplusHandle;
  final String? timezone;
  final bool isCrew;
  final bool isAdmin;
  final bool canManageScores;

  PlayerMe({
    required this.discordId,
    required this.displayName,
    this.golfplusHandle,
    this.timezone,
    this.isCrew = false,
    this.isAdmin = false,
    this.canManageScores = false,
  });

  factory PlayerMe.fromJson(Map<String, dynamic> j) => PlayerMe(
        discordId: j['discord_id'].toString(),
        displayName: (j['display_name'] ?? j['discord_id']).toString(),
        golfplusHandle: j['golfplus_handle']?.toString(),
        timezone: j['timezone']?.toString(),
        isCrew: j['is_crew'] == true,
        isAdmin: j['is_admin'] == true,
        canManageScores: j['can_manage_scores'] == true,
      );
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
        players: ((j['players'] as List?) ?? [])
            .map((e) => CasualPlayer.fromJson(e as Map<String, dynamic>))
            .toList(),
      );

  bool isIn(String discordId) =>
      players.any((p) => p.discordId == discordId);

  bool get isFull => players.length >= maxPlayers;

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

  CasualPlayer({required this.discordId, required this.displayName});

  factory CasualPlayer.fromJson(Map<String, dynamic> j) => CasualPlayer(
        discordId: j['discord_id'].toString(),
        displayName: (j['display_name'] ?? j['discord_id']).toString(),
      );
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
  final String teamName;
  final List<String> memberDiscordIds;
  final List<String> memberNames;
  final int size;
  final String displayName;

  MatchPlaySide({
    required this.id,
    required this.sideNumber,
    required this.teamName,
    required this.memberDiscordIds,
    required this.memberNames,
    required this.size,
    required this.displayName,
  });

  factory MatchPlaySide.fromJson(Map<String, dynamic> j) => MatchPlaySide(
        id: j['id'].toString(),
        sideNumber: (j['side_number'] as num?)?.toInt() ?? 1,
        teamName: (j['team_name'] ?? '').toString(),
        memberDiscordIds: ((j['member_discord_ids'] as List?) ?? [])
            .map((e) => e.toString())
            .toList(),
        memberNames: ((j['member_names'] as List?) ?? [])
            .map((e) => e.toString())
            .toList(),
        size: (j['size'] as num?)?.toInt() ?? 0,
        displayName: (j['display_name'] ?? '').toString(),
      );

  bool isMember(String discordId) => memberDiscordIds.contains(discordId);
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
  final int wins;
  final int losses;
  final int ties;
  final double winPct;

  MatchPlayRecord({
    required this.discordId,
    required this.playerName,
    required this.wins,
    required this.losses,
    required this.ties,
    required this.winPct,
  });

  factory MatchPlayRecord.fromJson(Map<String, dynamic> j) =>
      MatchPlayRecord(
        discordId: j['discord_id'].toString(),
        playerName: (j['player_name'] ?? j['discord_id']).toString(),
        wins: (j['wins'] as num?)?.toInt() ?? 0,
        losses: (j['losses'] as num?)?.toInt() ?? 0,
        ties: (j['ties'] as num?)?.toInt() ?? 0,
        winPct: (j['win_pct'] as num?)?.toDouble() ?? 0.0,
      );

  String get recordLine => '$wins–$losses–$ties';

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
  final String teamName;
  final List<String> playerNames;
  final List<String> memberDiscordIds;
  final int teamSize;
  final String displayName;
  final AltShotScore? score;

  AltShotTeam({
    required this.id,
    required this.player1DiscordId,
    required this.player1Name,
    required this.teamName,
    required this.playerNames,
    required this.memberDiscordIds,
    required this.teamSize,
    required this.displayName,
    this.score,
  });

  factory AltShotTeam.fromJson(Map<String, dynamic> j) => AltShotTeam(
        id: j['id'].toString(),
        player1DiscordId: (j['player1_discord_id'] ?? '').toString(),
        player1Name: (j['player1_name'] ?? '').toString(),
        teamName: (j['team_name'] ?? '').toString(),
        playerNames: ((j['player_names'] as List?) ?? [])
            .map((e) => e.toString())
            .toList(),
        memberDiscordIds: ((j['member_discord_ids'] as List?) ?? [])
            .map((e) => e.toString())
            .toList(),
        teamSize: (j['team_size'] as num?)?.toInt() ?? 1,
        displayName: (j['display_name'] ?? '').toString(),
        score: j['score'] == null
            ? null
            : AltShotScore.fromJson(j['score'] as Map<String, dynamic>),
      );

  /// A team needs at least 2 players to submit a score to the records.
  bool get canSubmit => teamSize >= 2;

  bool isMember(String discordId) => memberDiscordIds.contains(discordId);

  /// Subtitle line: every player on the team.
  String get playersLine => playerNames.join(' · ');
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

  bool get isFull => isFixedRoster
      ? teams.isNotEmpty && teams.first.teamSize >= (teamSize ?? 99)
      : teams.length >= maxTeams;

  /// Fixed-roster 1-team tee times: the single team has a set roster size.
  bool get isFixedRoster => teamSize != null;

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
  final String teamName;
  final List<String> playerNames;
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
    required this.teamName,
    required this.playerNames,
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
        teamName: (j['team_name'] ?? '').toString(),
        playerNames: ((j['player_names'] as List?) ?? [])
            .map((e) => e.toString())
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

  /// Subtitle: every player on the team (plus the team name when set).
  String get playersLine => playerNames.join(' · ');

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

// ignore_for_file: dangling_library_doc_comments
/// Shared data models for the Alpenglow VGC companion app.
///
/// Field names mirror the FastAPI backend's JSON keys. Nullable where the
/// backend may omit them.

/// One round of a multi-round tournament: same course as the tournament,
/// with its own tee/pin/wind settings. Green speed is tournament-wide.
class TournamentRound {
  final int roundNumber;
  final String? teePosition;
  final String? pinPosition;
  final String? windStrength;

  TournamentRound({
    required this.roundNumber,
    this.teePosition,
    this.pinPosition,
    this.windStrength,
  });

  factory TournamentRound.fromJson(Map<String, dynamic> j) => TournamentRound(
        roundNumber: (j['round_number'] as num?)?.toInt() ?? 1,
        teePosition: j['tee_position']?.toString(),
        pinPosition: j['pin_position']?.toString(),
        windStrength: j['wind_strength']?.toString(),
      );

  String get settingsSummary {
    final parts = <String>[];
    if (teePosition != null) parts.add('${_cap(teePosition!)} tees');
    if (pinPosition != null) parts.add('${_cap(pinPosition!)} pins');
    if (windStrength != null) parts.add('${_cap(windStrength!)} wind');
    return parts.join(' · ');
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
  final List<TeeTimePlayer> players;

  TeeTime({
    required this.id,
    required this.label,
    required this.startsAtUtc,
    required this.maxPlayers,
    required this.createdBy,
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
  final List<int> scores;
  final int? total;
  final int? toPar;
  final String? status;
  final String? submittedBy;
  final int roundNumber;

  Scorecard({
    required this.playerDiscordId,
    required this.scores,
    this.total,
    this.toPar,
    this.status,
    this.submittedBy,
    this.roundNumber = 1,
  });

  factory Scorecard.fromJson(Map<String, dynamic> j) => Scorecard(
        playerDiscordId: j['player_discord_id'].toString(),
        scores: ((j['scores'] as List?) ?? [])
            .map((e) => (e as num).toInt())
            .toList(),
        total: (j['total'] as num?)?.toInt(),
        toPar: (j['to_par'] as num?)?.toInt(),
        status: j['status']?.toString(),
        submittedBy: j['submitted_by']?.toString(),
        roundNumber: (j['round_number'] as num?)?.toInt() ?? 1,
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
}

class PlayerMe {
  final String discordId;
  final String displayName;
  final String? golfplusHandle;
  final String? timezone;
  final bool isCrew;

  PlayerMe({
    required this.discordId,
    required this.displayName,
    this.golfplusHandle,
    this.timezone,
    this.isCrew = false,
  });

  factory PlayerMe.fromJson(Map<String, dynamic> j) => PlayerMe(
        discordId: j['discord_id'].toString(),
        displayName: (j['display_name'] ?? j['discord_id']).toString(),
        golfplusHandle: j['golfplus_handle']?.toString(),
        timezone: j['timezone']?.toString(),
        isCrew: j['is_crew'] == true,
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

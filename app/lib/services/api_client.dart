import 'dart:convert';

import 'package:http/http.dart' as http;

import '../models/models.dart';

/// Error thrown for non-2xx API responses. [code] is the backend's
/// machine-readable error code (e.g. "timezone_required"), when present.
class ApiException implements Exception {
  final String message;
  final String? code;
  final int statusCode;

  ApiException(this.message, {this.code, required this.statusCode});

  @override
  String toString() => message;
}

/// Thrown when a player's shot stats are private to everyone but them.
/// Maps the backend's 403 {"code": "stats_private"}.
class StatsPrivateException implements Exception {
  final String playerKey;

  StatsPrivateException(this.playerKey);

  @override
  String toString() => 'Stats are private';
}

/// Friendly message mapping for known backend error codes.
String friendlyApiMessage(ApiException e) {
  switch (e.code) {
    case 'timezone_required':
      return 'Set your timezone in Profile first.';
    case 'registration_closed':
      return 'Registration is closed for this tournament.';
    case 'request_pending':
      return 'You already have a pending join request.';
    case 'tee_time_not_passed':
      return 'Scores can only be entered after the tee time.';
    case 'already_registered':
      return 'You are already registered.';
    case 'not_in_tee_time':
      return "You're not in this tee time — join it first, then enter scores.";
    case 'player_not_in_tee_time':
      return "That player isn't in this tee time.";
    case 'scorecard_locked':
      return 'That scorecard is already submitted — only crew (admins, mods, tournament directors) can change it.';
    case 'round_not_started':
      return 'That round hasn\'t started yet — wait for its window to open.';
    case 'round_ended':
      return 'That round has closed — scores are locked. Ask a crew member if a card still needs to go in.';
    case 'round_conflict':
      return 'You\'re already in another tee time for that round — leave it first to join this one.';
    case 'round_already_submitted':
      return 'A card for that round was already submitted — one scorecard per round per player.';
    case 'active_season_exists':
      return 'A season is already active — end it before starting a new one.';
    default:
      return e.message;
  }
}

/// Thin HTTP wrapper for the Alpenglow VGC backend.
///
/// Every request sends `Authorization: Bearer <token>` — either the Discord
/// OAuth access token or a JWT from the local email login.
/// [baseUrl] defaults to http://localhost:8420 and is configurable.
class ApiClient {
  final String baseUrl;
  final String token;

  ApiClient({required this.baseUrl, required this.token});

  Uri _uri(String path, [Map<String, String>? query]) {
    final base = baseUrl.endsWith('/')
        ? baseUrl.substring(0, baseUrl.length - 1)
        : baseUrl;
    return Uri.parse('$base$path').replace(queryParameters: query);
  }

  Map<String, String> get _headers => {
        'Authorization': 'Bearer $token',
        'Content-Type': 'application/json',
        'Accept': 'application/json',
      };

  dynamic _decode(http.Response res) {
    dynamic body;
    if (res.body.isNotEmpty) {
      try {
        body = jsonDecode(res.body);
      } catch (_) {
        body = res.body;
      }
    }
    if (res.statusCode >= 200 && res.statusCode < 300) return body;
    String message = 'Request failed (${res.statusCode})';
    String? code;
    if (body is Map<String, dynamic>) {
      code = body['code']?.toString() ?? body['error_code']?.toString();
      final detail = body['detail'] ?? body['message'] ?? body['error'];
      if (detail != null) message = detail.toString();
      if (code != null && message.startsWith('Request failed')) {
        message = 'Request failed: $code';
      }
    }
    throw ApiException(message, code: code, statusCode: res.statusCode);
  }

  Future<dynamic> _get(String path, [Map<String, String>? query]) async {
    final res = await http.get(_uri(path, query), headers: _headers);
    return _decode(res);
  }

  Future<dynamic> _post(String path, [Map<String, dynamic>? json]) async {
    final res = await http.post(_uri(path),
        headers: _headers, body: json == null ? null : jsonEncode(json));
    return _decode(res);
  }

  Future<dynamic> _put(String path, Map<String, dynamic> json) async {
    final res =
        await http.put(_uri(path), headers: _headers, body: jsonEncode(json));
    return _decode(res);
  }

  Future<dynamic> _patch(String path, Map<String, dynamic> json) async {
    final res =
        await http.patch(_uri(path), headers: _headers, body: jsonEncode(json));
    return _decode(res);
  }

  Future<dynamic> _delete(String path,
      [Map<String, String>? query]) async {
    final res = await http.delete(_uri(path, query), headers: _headers);
    return _decode(res);
  }

  // --- Tournaments ---------------------------------------------------------

  Future<List<Tournament>> getTournaments() async {
    final body = await _get('/api/tournaments');
    final list = body is List ? body : (body['tournaments'] as List? ?? []);
    return list
        .map((e) => Tournament.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  Future<Tournament> createTournament({
    required String name,
    required String format,
    required int holes,
    required String course,
    required String startDate,
    required String endDate,
    String teePosition = 'middle',
    String pinPosition = 'white',
    String windStrength = 'moderate',
    String greenSpeed = 'pro',
    String? description,
    List<Map<String, String>>? rounds,
  }) async {
    final payload = <String, dynamic>{
      'name': name,
      'format': format,
      'holes': holes,
      'course': course,
      'start_date': startDate,
      'end_date': endDate,
      'tee_position': teePosition,
      'pin_position': pinPosition,
      'wind_strength': windStrength,
      'green_speed': greenSpeed,
      if (description != null && description.isNotEmpty)
        'description': description,
    };
    if (rounds != null) payload['rounds'] = rounds;
    final body = await _post('/api/tournaments', payload);
    return Tournament.fromJson(body as Map<String, dynamic>);
  }

  Future<List<GolfCourse>> getCourses() async {
    final body = await _get('/api/courses');
    final list = body as List? ?? [];
    return list
        .map((e) => GolfCourse.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  Future<Map<String, dynamic>> register(String tournamentId) async {
    final body = await _post('/api/tournaments/$tournamentId/register');
    return (body as Map<String, dynamic>? ?? {});
  }

  Future<void> unregister(String tournamentId) async {
    await _delete('/api/tournaments/$tournamentId/register');
  }

  /// Edit tournament details (crew). Only include keys that changed.
  Future<Tournament> editTournament(
      String tournamentId, Map<String, dynamic> fields) async {
    final body =
        await _patch('/api/tournaments/$tournamentId', fields) as Map<String, dynamic>;
    return Tournament.fromJson(body);
  }

  /// Finalize a tournament: posts final standings + awards season points (crew).
  Future<void> completeTournament(String tournamentId) async {
    await _post('/api/tournaments/$tournamentId/complete');
  }

  /// Close a tournament immediately, no standings or points (crew).
  Future<void> endTournament(String tournamentId) async {
    await _post('/api/tournaments/$tournamentId/end');
  }

  /// Permanently delete a tournament and everything under it (admins only).
  Future<void> deleteTournament(String tournamentId) async {
    await _delete('/api/tournaments/$tournamentId');
  }

  Future<List<TeeTime>> getTeeTimes(String tournamentId) async {
    final body = await _get('/api/tournaments/$tournamentId/tee-times');
    final list = body is List ? body : (body['tee_times'] as List? ?? []);
    return list.map((e) => TeeTime.fromJson(e as Map<String, dynamic>)).toList();
  }

  Future<TeeTime> createTeeTime(String tournamentId,
      {required String label,
      required String date,
      required String time,
      required int maxPlayers,
      int roundNumber = 1}) async {
    final body = await _post('/api/tournaments/$tournamentId/tee-times', {
      'label': label,
      'date': date,
      'time': time,
      'max_players': maxPlayers,
      'round_number': roundNumber,
    });
    final m = (body is Map && body['tee_time'] is Map)
        ? body['tee_time'] as Map<String, dynamic>
        : (body as Map<String, dynamic>);
    return TeeTime.fromJson(m);
  }

  /// Edit a tee time (creator or crew). Only include keys that changed:
  /// label, date (YYYY-MM-DD), time (HH:MM 24h), round_number.
  Future<TeeTime> editTeeTime(
      String teeTimeId, Map<String, dynamic> fields) async {
    final body =
        await _patch('/api/tee-times/$teeTimeId', fields) as Map<String, dynamic>;
    final m = (body['tee_time'] is Map)
        ? body['tee_time'] as Map<String, dynamic>
        : body;
    return TeeTime.fromJson(m);
  }

  /// Delete a tee time (creator or crew). Refused when scores exist.
  Future<void> deleteTeeTime(String teeTimeId) async {
    await _delete('/api/tee-times/$teeTimeId');
  }

  Future<void> joinTeeTime(String teeTimeId) async {    await _post('/api/tee-times/$teeTimeId/join');
  }

  Future<void> leaveTeeTime(String teeTimeId) async {
    await _post('/api/tee-times/$teeTimeId/leave');
  }

  Future<void> requestTeeTime(String teeTimeId) async {
    await _post('/api/tee-times/$teeTimeId/request');
  }

  Future<List<TeeTimeRequest>> getTeeTimeRequests(String teeTimeId) async {
    final body = await _get('/api/tee-times/$teeTimeId/requests');
    final list = body is List ? body : (body['requests'] as List? ?? []);
    return list
        .map((e) => TeeTimeRequest.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  Future<void> approveRequest(String teeTimeId, String requestId) async {
    await _post('/api/tee-times/$teeTimeId/requests/$requestId/approve');
  }

  Future<void> declineRequest(String teeTimeId, String requestId) async {
    await _post('/api/tee-times/$teeTimeId/requests/$requestId/decline');
  }

  Future<Scorecard?> getScorecard(String teeTimeId,
      {int? roundNumber, String? playerDiscordId}) async {
    final params = <String>[];
    if (roundNumber != null) params.add('round_number=$roundNumber');
    if (playerDiscordId != null) {
      params.add('player_discord_id=$playerDiscordId');
    }
    final path = params.isEmpty
        ? '/api/tee-times/$teeTimeId/scorecard'
        : '/api/tee-times/$teeTimeId/scorecard?${params.join('&')}';
    final body = await _get(path);
    if (body is! Map<String, dynamic>) return null;
    final card = body['card'];
    if (card == null) return null;
    return Scorecard.fromJson(card as Map<String, dynamic>);
  }

  Future<Scorecard> submitScorecard(
      String teeTimeId, String playerDiscordId, List<int?> scores,
      {int roundNumber = 1, String? witnessName, bool complete = false}) async {
    final body = await _put('/api/tee-times/$teeTimeId/scorecard', {
      'player_discord_id': playerDiscordId,
      'scores': scores,
      'round_number': roundNumber,
      'complete': complete,
      if (witnessName != null && witnessName.trim().isNotEmpty)
        'witness_name': witnessName.trim(),
    });
    return Scorecard.fromJson(
        (body as Map<String, dynamic>)['card'] as Map<String, dynamic>);
  }

  // --- Shot-by-shot tracking (opt-in) --------------------------------------

  /// Shots tracked on one hole of a scorecard, in seq order.
  Future<List<Shot>> getHoleShots(int cardId, int hole) async {
    final body =
        await _get('/api/scorecards/$cardId/holes/$hole/shots');
    final list = (body as Map<String, dynamic>?)?['shots'] as List? ?? [];
    return list
        .map((e) => Shot.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  /// Replace the tracked shots for one hole (empty list clears them).
  /// Returns the saved shot count.
  Future<int> putHoleShots(
      int cardId, int hole, List<Shot> shots) async {
    final body = await _put('/api/scorecards/$cardId/holes/$hole/shots', {
      'shots': shots.map((s) => s.toJson()).toList(),
    });
    return ((body as Map<String, dynamic>?)?['shots'] as num?)?.toInt() ??
        shots.length;
  }

  /// Patch one round's Golf+ settings and/or date window (crew).
  Future<Tournament> editRound(String tournamentId, int roundNumber,
      Map<String, dynamic> fields) async {
    final body = await _patch(
            '/api/tournaments/$tournamentId/rounds/$roundNumber', fields)
        as Map<String, dynamic>;
    return Tournament.fromJson(body);
  }

  Future<List<LeaderboardEntry>> getLeaderboard(String tournamentId) async {
    final body = await _get('/api/tournaments/$tournamentId/leaderboard');
    if (body is! Map<String, dynamic>) return [];
    // Ranked standings first, then pending (unverified solo) cards.
    final standings = (body['standings'] as List? ?? []);
    final pending = (body['pending'] as List? ?? []);
    return [...standings, ...pending]
        .map((e) => LeaderboardEntry(e as Map<String, dynamic>))
        .toList();
  }

  Future<Map<String, dynamic>> getSeasonStandings() async {
    final body = await _get('/api/seasons/standings');
    return (body as Map<String, dynamic>? ?? {});
  }

  /// Typed season points standings. Returns [SeasonStandings.empty] when
  /// there is no active season (API 404 no_active_season) instead of
  /// throwing, so the UI can show a friendly empty state.
  Future<SeasonStandings> seasonStandings() async {
    try {
      final body = await _get('/api/seasons/standings');
      final map = body as Map<String, dynamic>? ?? {};
      final season = map['season'] as Map<String, dynamic>?;
      final items = (map['standings'] as List? ?? []);
      return SeasonStandings(
        seasonId: (season?['id'] as num?)?.toInt(),
        seasonName: (season?['name'] ?? '').toString(),
        seasonStartDate: season?['start_date']?.toString(),
        seasonEndDate: season?['end_date']?.toString(),
        entries: items
            .whereType<Map<String, dynamic>>()
            .map(SeasonStandingEntry.fromJson)
            .toList(growable: false),
      );
    } on ApiException catch (e) {
      if (e.statusCode == 404) return const SeasonStandings.empty();
      rethrow;
    }
  }

  Future<Map<String, dynamic>> createSeason({
    required String name,
    String? startDate,
    String? endDate,
  }) async {
    final payload = <String, dynamic>{'name': name};
    if (startDate != null) payload['start_date'] = startDate;
    if (endDate != null) payload['end_date'] = endDate;
    final body = await _post('/api/seasons', payload);
    return (body as Map<String, dynamic>?) ?? {};
  }

  Future<Map<String, dynamic>> completeSeason(int seasonId) async {
    final body = await _post('/api/seasons/$seasonId/complete');
    return (body as Map<String, dynamic>?) ?? {};
  }

  // --- Players -------------------------------------------------------------

  Future<PlayerMe> getMe() async {
    final body = await _get('/api/players/me');
    return PlayerMe.fromJson(body as Map<String, dynamic>);
  }

  Future<void> updateMe(
      {String? timezone, String? golfplusHandle, bool? statsPrivate}) async {
    final payload = <String, dynamic>{};
    if (timezone != null) payload['timezone'] = timezone;
    if (golfplusHandle != null) payload['golfplus_handle'] = golfplusHandle;
    if (statsPrivate != null) payload['stats_private'] = statsPrivate;
    await _patch('/api/players/me', payload);
  }

  Future<Map<String, dynamic>> getMyStats() async {
    final body = await _get('/api/players/me/stats');
    return (body as Map<String, dynamic>? ?? {});
  }

  /// Shot-tracking stats + WHS-lite handicap for any player. [key] is a
  /// discord id, a `local:<hex>` key, or "me". Throws [StatsPrivateException]
  /// when the player's stats are private to everyone but them.
  Future<PlayerShotStats> getPlayerStats(String key) async {
    try {
      final body = await _get('/api/players/$key/stats');
      return PlayerShotStats.fromJson(body as Map<String, dynamic>);
    } on ApiException catch (e) {
      if (e.statusCode == 403 && e.code == 'stats_private') {
        throw StatsPrivateException(key);
      }
      rethrow;
    }
  }

  /// Round history: the player's completed/submitted rounds (tournament +
  /// casual), newest tee time first. [key] is a discord id, a `local:<hex>`
  /// key, or "me".
  Future<List<RoundSummary>> getRoundHistory(String key) async {
    final body = await _get('/api/players/$key/rounds');
    final rounds = (body as Map<String, dynamic>)['rounds'];
    if (rounds is! List) return const [];
    return rounds
        .whereType<Map<String, dynamic>>()
        .map(RoundSummary.fromJson)
        .toList();
  }

  /// Permanently delete the caller's own player record (auth required).
  /// Returns the raw response body (e.g. {"deleted": true, "discord_id": ...}).
  Future<Map<String, dynamic>> deleteAccount() async {
    final body = await _delete('/api/players/me');
    return (body as Map<String, dynamic>? ?? {});
  }

  // --- Local email auth (public; no token needed) ------------------------------

  /// Create an email+password account. Returns {token, player}.
  Future<Map<String, dynamic>> localSignup({
    required String email,
    required String password,
    required String displayName,
  }) async {
    final body = await _post('/api/auth/signup', {
      'email': email,
      'password': password,
      'display_name': displayName,
    });
    return (body as Map<String, dynamic>? ?? {});
  }

  /// Email+password sign in. Returns {token, player}.
  Future<Map<String, dynamic>> localLogin({
    required String email,
    required String password,
  }) async {
    final body = await _post('/api/auth/login', {
      'email': email,
      'password': password,
    });
    return (body as Map<String, dynamic>? ?? {});
  }

  // --- Admin -----------------------------------------------------------------

  /// List all players with their crew roles (admin only).
  Future<List<CrewPlayer>> getCrewPlayers() async {
    final body = await _get('/api/admin/players');
    final list = (body as Map<String, dynamic>?)?['players'] as List? ?? [];
    return list
        .map((e) => CrewPlayer.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  /// Grant or revoke a crew role on a player (admin only).
  /// [role] is "Mod" or "Tournament Director"; [action] is "grant" or "revoke".
  Future<Map<String, dynamic>> setCrewRole({
    required String discordId,
    required String role,
    required String action,
  }) async {
    final body = await _post('/api/admin/crew/roles', {
      'discord_id': discordId,
      'role': role,
      'action': action,
    });
    return (body as Map<String, dynamic>? ?? {});
  }

  /// Grant or revoke the admin flag on a local (email) account (admin only).
  Future<Map<String, dynamic>> setLocalAdmin({
    required String playerKey,
    required bool isAdmin,
  }) async {
    final body = await _post('/api/admin/users/$playerKey/admin', {
      'is_admin': isAdmin,
    });
    return (body as Map<String, dynamic>? ?? {});
  }

  /// Generate a one-time temporary password for a local account (admin only).
  /// Returns the temp password string.
  Future<String> resetLocalPassword({required String playerKey}) async {
    final body = await _post('/api/admin/users/$playerKey/reset-password');
    return (body as Map<String, dynamic>?)?['temp_password']?.toString() ?? '';
  }

  // --- Push notifications --------------------------------------------------

  Future<void> registerDevice(String pushToken) async {
    await _post('/api/devices/register',
        {'push_token': pushToken, 'platform': 'ios'});
  }

  Future<void> unregisterDevice(String pushToken) async {
    await _delete('/api/devices', {'push_token': pushToken});
  }

  Future<Map<String, bool>> getNotificationPrefs() async {
    final body = await _get('/api/notifications/prefs');
    final map = body as Map<String, dynamic>? ?? {};
    return {
      for (final k in [
        'tournament_starts',
        'round_starts',
        'ace',
        'albatross',
        'top3_changes'
      ])
        k: map[k] == true,
    };
  }

  Future<Map<String, bool>> updateNotificationPrefs(
      Map<String, bool> prefs) async {
    final body = await _put('/api/notifications/prefs', prefs);
    final map = body as Map<String, dynamic>? ?? {};
    return {
      for (final k in prefs.keys) k: map[k] == true,
    };
  }

  // --- Casual tee times ----------------------------------------------------

  Future<List<CasualTeeTime>> getCasualTeeTimes() async {
    final body = await _get('/api/casual-tee-times');
    final list = (body as Map<String, dynamic>)['tee_times'] as List? ?? [];
    return list
        .map((e) => CasualTeeTime.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  Future<CasualTeeTime> getCasualTeeTime(String id) async {
    final body = await _get('/api/casual-tee-times/$id');
    return CasualTeeTime.fromJson(body as Map<String, dynamic>);
  }

  Future<CasualTeeTime> createCasualTeeTime(Map<String, dynamic> payload) async {
    final body = await _post('/api/casual-tee-times', payload);
    return CasualTeeTime.fromJson(body as Map<String, dynamic>);
  }

  Future<CasualTeeTime> updateCasualTeeTime(
      String id, Map<String, dynamic> payload) async {
    final body = await _put('/api/casual-tee-times/$id', payload);
    return CasualTeeTime.fromJson(body as Map<String, dynamic>);
  }

  Future<void> deleteCasualTeeTime(String id) async {
    await _delete('/api/casual-tee-times/$id');
  }

  Future<CasualTeeTime> joinCasualTeeTime(String id) async {
    final body = await _post('/api/casual-tee-times/$id/join');
    return CasualTeeTime.fromJson(body as Map<String, dynamic>);
  }

  Future<CasualTeeTime> leaveCasualTeeTime(String id) async {
    final body = await _post('/api/casual-tee-times/$id/leave');
    return CasualTeeTime.fromJson(body as Map<String, dynamic>);
  }

  /// Casual scorecards (stroke / best-ball formats only).
  Future<List<Scorecard>> getCasualScorecards(String id) async {
    final body = await _get('/api/casual-tee-times/$id/scorecards');
    final list = (body as Map<String, dynamic>)['scorecards'] as List? ?? [];
    return list
        .map((e) => Scorecard.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  Future<Scorecard?> getCasualScorecard(String id,
      {String? playerDiscordId}) async {
    var path = '/api/casual-tee-times/$id/scorecard';
    if (playerDiscordId != null) {
      path += '?player_discord_id=${Uri.encodeComponent(playerDiscordId)}';
    }
    final body = await _get(path);
    final card = (body as Map<String, dynamic>)['card'];
    return card == null
        ? null
        : Scorecard.fromJson(card as Map<String, dynamic>);
  }

  Future<Scorecard> submitCasualScorecard(
      String id, String playerDiscordId, List<int?> scores,
      {bool complete = false, String? witnessName}) async {
    final body = await _put('/api/casual-tee-times/$id/scorecard', {
      'player_discord_id': playerDiscordId,
      'scores': scores,
      'complete': complete,
      if (witnessName != null && witnessName.isNotEmpty)
        'witness_name': witnessName,
    });
    return Scorecard.fromJson(
        (body as Map<String, dynamic>)['card'] as Map<String, dynamic>);
  }

  Future<Map<String, dynamic>> getCasualLeaderboard(String id) async {
    final body = await _get('/api/casual-tee-times/$id/leaderboard');
    return body as Map<String, dynamic>;
  }

  // ------------------------------------------------------------- alt-shot
  Future<List<AltShotTeeTime>> listAltShotTeeTimes() async {
    final body = await _get('/api/altshot-tee-times');
    final items = (body as Map<String, dynamic>)['tee_times'] as List;
    return items
        .map((e) => AltShotTeeTime.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  Future<AltShotTeeTime> getAltShotTeeTime(String id) async {
    final body = await _get('/api/altshot-tee-times/$id');
    return AltShotTeeTime.fromJson(body as Map<String, dynamic>);
  }

  Future<AltShotTeeTime> createAltShotTeeTime(
      Map<String, dynamic> payload) async {
    final body = await _post('/api/altshot-tee-times', payload);
    return AltShotTeeTime.fromJson(body as Map<String, dynamic>);
  }

  Future<AltShotTeeTime> updateAltShotTeeTime(
      String id, Map<String, dynamic> payload) async {
    final body = await _patch('/api/altshot-tee-times/$id', payload);
    return AltShotTeeTime.fromJson(body as Map<String, dynamic>);
  }

  Future<void> deleteAltShotTeeTime(String id) async {
    await _delete('/api/altshot-tee-times/$id');
  }

  Future<AltShotTeeTime> joinAltShotTeeTime(String id,
      {List<String> extraNames = const [], String? teamId}) async {
    final body = await _post('/api/altshot-tee-times/$id/join', {
      'extra_names': extraNames,
      if (teamId case final tid) 'team_id': tid,
    });
    return AltShotTeeTime.fromJson(body as Map<String, dynamic>);
  }

  /// Move to another team of a fixed 2-team tee time (before scoring).
  Future<AltShotTeeTime> switchAltShotTeam(String id, String teamId) async {
    final body = await _post(
        '/api/altshot-tee-times/$id/switch-team', {'team_id': teamId});
    return AltShotTeeTime.fromJson(body as Map<String, dynamic>);
  }

  Future<AltShotTeeTime> leaveAltShotTeeTime(String id) async {
    final body = await _post('/api/altshot-tee-times/$id/leave');
    return AltShotTeeTime.fromJson(body as Map<String, dynamic>);
  }

  Future<AltShotTeam> updateAltShotTeam(
      String ttId, String teamId, Map<String, dynamic> payload) async {
    final body = await _patch(
        '/api/altshot-tee-times/$ttId/teams/$teamId', payload);
    return AltShotTeam.fromJson(body as Map<String, dynamic>);
  }

  /// Organizer/crew management of a fixed 2-team tee time's team: rename,
  /// move a player, or remove a player. Returns the full tee time since
  /// moves affect both teams.
  Future<AltShotTeeTime> manageAltShotTeam(
      String ttId, String teamId, Map<String, dynamic> payload) async {
    final body = await _patch(
        '/api/altshot-tee-times/$ttId/teams/$teamId', payload);
    return AltShotTeeTime.fromJson(body as Map<String, dynamic>);
  }

  Future<Map<String, dynamic>> submitAltShotScore(
      String ttId, String teamId, List<int> holes) async {
    final body = await _post(
        '/api/altshot-tee-times/$ttId/teams/$teamId/score', {'holes': holes});
    return body as Map<String, dynamic>;
  }

  Future<void> deleteAltShotScore(String ttId, String teamId) async {
    await _delete('/api/altshot-tee-times/$ttId/teams/$teamId/score');
  }

  Future<List<AltShotRecord>> getAltShotRecords(String course, int teamSize,
      {String teePosition = 'back',
      String pinPosition = 'black',
      String windStrength = 'moderate',
      String greenSpeed = 'pro'}) async {
    final body = await _get('/api/altshot-records', {
      'course': course,
      'team_size': teamSize.toString(),
      'tee_position': teePosition,
      'pin_position': pinPosition,
      'wind_strength': windStrength,
      'green_speed': greenSpeed,
    });
    final items = (body as Map<String, dynamic>)['records'] as List;
    return items
        .map((e) => AltShotRecord.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  /// Batched best-record-per-course lookup for the tee-time course picker:
  /// {course name: record}. One call, keyed by roster size and setup.
  Future<Map<String, AltShotCourseRecord>> getAltShotRecordsSummary(
      int teamSize,
      {String teePosition = 'back',
      String pinPosition = 'black',
      String windStrength = 'moderate',
      String greenSpeed = 'pro'}) async {
    final body = await _get('/api/altshot-records/summary', {
      'team_size': teamSize.toString(),
      'tee_position': teePosition,
      'pin_position': pinPosition,
      'wind_strength': windStrength,
      'green_speed': greenSpeed,
    });
    final recs = (body as Map<String, dynamic>)['records'] as Map;
    return {
      for (final e in recs.entries)
        e.key.toString(): AltShotCourseRecord.fromJson(
            e.value as Map<String, dynamic>)
    };
  }

  // ------------------------------------------------------------ match-play
  Future<List<MatchPlayTeeTime>> listMatchPlayTeeTimes() async {
    final body = await _get('/api/matchplay/tee-times');
    final items = (body as Map<String, dynamic>)['tee_times'] as List;
    return items
        .map((e) => MatchPlayTeeTime.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  Future<MatchPlayTeeTime> getMatchPlayTeeTime(String id) async {
    final body = await _get('/api/matchplay/tee-times/$id');
    return MatchPlayTeeTime.fromJson(body as Map<String, dynamic>);
  }

  Future<MatchPlayTeeTime> createMatchPlayTeeTime(
      Map<String, dynamic> payload) async {
    final body = await _post('/api/matchplay/tee-times', payload);
    return MatchPlayTeeTime.fromJson(body as Map<String, dynamic>);
  }

  Future<MatchPlayTeeTime> updateMatchPlayTeeTime(
      String id, Map<String, dynamic> payload) async {
    final body = await _patch('/api/matchplay/tee-times/$id', payload);
    return MatchPlayTeeTime.fromJson(body as Map<String, dynamic>);
  }

  Future<void> deleteMatchPlayTeeTime(String id) async {
    await _delete('/api/matchplay/tee-times/$id');
  }

  Future<MatchPlayTeeTime> joinMatchPlayTeeTime(String id,
      {int sideNumber = 1}) async {
    final body = await _post('/api/matchplay/tee-times/$id/join',
        {'side_number': sideNumber});
    return MatchPlayTeeTime.fromJson(body as Map<String, dynamic>);
  }

  Future<MatchPlayTeeTime> leaveMatchPlayTeeTime(String id) async {
    final body = await _post('/api/matchplay/tee-times/$id/leave');
    return MatchPlayTeeTime.fromJson(body as Map<String, dynamic>);
  }

  Future<MatchPlayScore?> getMatchPlayScore(String id) async {
    final body = await _get('/api/matchplay/tee-times/$id/score');
    final s = (body as Map<String, dynamic>)['score'];
    return s == null
        ? null
        : MatchPlayScore.fromJson(s as Map<String, dynamic>);
  }

  /// Live-save the 18 hole results (+1 / -1 / 0 / null). The server
  /// recomputes lead, status, and result text on every save.
  Future<MatchPlayScore> saveMatchPlayScore(
      String id, List<int?> holeResults) async {
    final body = await _put('/api/matchplay/tee-times/$id/score',
        {'hole_results': holeResults});
    return MatchPlayScore.fromJson(
        (body as Map<String, dynamic>)['score'] as Map<String, dynamic>);
  }

  Future<void> deleteMatchPlayScore(String id) async {
    await _delete('/api/matchplay/tee-times/$id/score');
  }

  Future<List<MatchPlayRecord>> getMatchPlayRecords(String format) async {
    final body = await _get('/api/matchplay/records', {
      'format': format,
    });
    final items = (body as Map<String, dynamic>)['records'] as List;
    return items
        .map((e) => MatchPlayRecord.fromJson(e as Map<String, dynamic>))
        .toList();
  }
}

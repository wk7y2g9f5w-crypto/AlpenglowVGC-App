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
    default:
      return e.message;
  }
}

/// Thin HTTP wrapper for the Alpenglow VGC backend.
///
/// Every request sends `Authorization: Bearer <discord_access_token>`.
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

  Future<dynamic> _delete(String path) async {
    final res = await http.delete(_uri(path), headers: _headers);
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
      required int maxPlayers}) async {
    final body = await _post('/api/tournaments/$tournamentId/tee-times', {
      'label': label,
      'date': date,
      'time': time,
      'max_players': maxPlayers,
    });
    final m = (body is Map && body['tee_time'] is Map)
        ? body['tee_time'] as Map<String, dynamic>
        : (body as Map<String, dynamic>);
    return TeeTime.fromJson(m);
  }

  /// Edit a tee time (creator or crew). Only include keys that changed:
  /// label, date (YYYY-MM-DD), time (HH:MM 24h).
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

  Future<void> submitScorecard(
      String teeTimeId, String playerDiscordId, List<int> scores,
      {int roundNumber = 1}) async {
    await _put('/api/tee-times/$teeTimeId/scorecard', {
      'player_discord_id': playerDiscordId,
      'scores': scores,
      'round_number': roundNumber,
    });
  }

  Future<List<LeaderboardEntry>> getLeaderboard(String tournamentId) async {
    final body = await _get('/api/tournaments/$tournamentId/leaderboard');
    final list = body is Map<String, dynamic>
        ? (body['entries'] as List? ?? [])
        : (body as List? ?? []);
    return list.map((e) => LeaderboardEntry(e as Map<String, dynamic>)).toList();
  }

  Future<Map<String, dynamic>> getSeasonStandings() async {
    final body = await _get('/api/seasons/standings');
    return (body as Map<String, dynamic>? ?? {});
  }

  // --- Players -------------------------------------------------------------

  Future<PlayerMe> getMe() async {
    final body = await _get('/api/players/me');
    return PlayerMe.fromJson(body as Map<String, dynamic>);
  }

  Future<void> updateMe({String? timezone, String? golfplusHandle}) async {
    final payload = <String, dynamic>{};
    if (timezone != null) payload['timezone'] = timezone;
    if (golfplusHandle != null) payload['golfplus_handle'] = golfplusHandle;
    await _patch('/api/players/me', payload);
  }

  Future<Map<String, dynamic>> getMyStats() async {
    final body = await _get('/api/players/me/stats');
    return (body as Map<String, dynamic>? ?? {});
  }
}

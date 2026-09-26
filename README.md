# Alpenglow VGC — Companion App

Flutter (single codebase, iOS + Android) companion app for the Alpenglow VGC
Golf+ VR tournament community. Talks to the FastAPI backend
(`~/workspace/golf-companion-backend/` — sibling project).

## Project layout

```
app/                        # Flutter project (flutter create output)
  lib/
    main.dart               # login gate + bottom-nav shell
    models/models.dart      # Tournament, TeeTime, Scorecard, …
    services/
      api_client.dart       # HTTP wrapper (Bearer auth, ApiException codes)
      auth.dart             # AuthService (secure token) + SettingsService
      timezones.dart        # common IANA zones + client-side validator
    screens/
      login_screen.dart     # Discord OAuth (implicit flow)
      tournaments_screen.dart
      tournament_detail_screen.dart  # header + register + Tee Times/Leaderboard tabs
      tee_time_detail_screen.dart    # players, join/leave/request, approvals
      score_entry_screen.dart        # tap-to-enter scorecard
      profile_screen.dart   # timezone, Golf+ handle, stats, registrations
      settings_screen.dart  # base URL, OAuth config, logout
    widgets/common.dart     # StatusChip, AsyncBody, formatters
  android/ ios/             # platform sources (complete; iOS not compiled here)
```

## Build

Prereqs: Flutter stable SDK, JDK 17, Android SDK (platform-tools,
`platforms;android-36`, `build-tools;36.0.0`).

```bash
cd app
flutter pub get
flutter analyze
flutter build apk --debug
# → build/app/outputs/flutter-apk/app-debug.apk
```

## Discord OAuth config (no secrets in code)

Client ID and redirect URI are **never hardcoded**. Two sources, in order:

1. `--dart-define` (preferred for release builds):
   ```
   flutter build apk --debug \
     --dart-define=DISCORD_CLIENT_ID=<id> \
     --dart-define=DISCORD_REDIRECT_URI=<uri>
   ```
2. Settings screen fields (persisted in shared_preferences) — also reachable
   from the login screen's setup hint before first login.

The redirect URI must be registered in the Discord developer portal.
Default convention: `com.alpenglowvgc.app://oauth-callback`.
Matching deep-link handlers are wired in:

- Android: `android/app/src/main/AndroidManifest.xml` (intent-filter for
  scheme `com.alpenglowvgc.app`, host `oauth-callback`)
- iOS: `ios/Runner/Info.plist` (`CFBundleURLSchemes: com.alpenglowvgc.app`)

If you use a different redirect URI, update both to match.

## API base URL

Default `http://localhost:8420` (Android emulator note: use
`http://10.0.2.2:8420` to reach the host machine). Change it in
Settings → API base URL; persisted across launches.

## Secure storage choice

The Discord access token is stored with **flutter_secure_storage**
(Keychain on iOS, EncryptedSharedPreferences on Android). Non-secret
settings (base URL, OAuth config) use shared_preferences.

## API contract notes / gaps

- Score entry needs hole pars; the contract's `GET /api/tournaments` does
  not document a `pars` field, so the app reads an optional `pars` array
  when present and falls back to numeric 1–15 entry otherwise. If the
  backend exposes pars elsewhere, point it out and this can be wired up.
- `GET /api/tee-times/{id}/scorecard` is assumed to return
  `{card: {...} | null}` per the contract.
- Leaderboard entries are rendered generically (`rank`, `display_name`,
  `total`, `to_par`) to tolerate shape differences.
- Season standings endpoint is implemented in `ApiClient.getSeasonStandings()`
  but has no dedicated screen yet (see v2).

## Stubbed / v2

- Season standings screen (client method exists).
- Push notifications for lead changes / tee-time reminders.
- Offline caching of tournaments/scorecards.
- Refresh-token flow (implicit flow tokens are short-lived; currently the
  user re-logs-in when the token expires → 401 surfaces as a load error).

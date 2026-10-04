#!/usr/bin/env bash
# Build the Flutter web PWA and stage it for FastAPI static serving.
#
# Branch-scoped: this script only runs from render.yaml's buildCommand on the
# `pwa` branch. The native (Codemagic/TestFlight) pipeline never touches it.
#
# Steps:
#   1. Ensure a Flutter SDK is available (download stable if missing).
#   2. `flutter build web --release` with the production dart-defines.
#   3. Strip hole-map assets from the service-worker precache manifest so
#      first load doesn't pull ~6.3MB of maps (they load on demand instead).
#   4. Copy the build output to api/static_web/ (gitignored), where the API
#      serves it from the same origin (no CORS needed).
#
# Required env: DISCORD_CLIENT_ID (Render env var — same value Codemagic uses).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SDK_DIR="${FLUTTER_SDK_DIR:-$HOME/flutter-sdk}"

if [[ -z "${DISCORD_CLIENT_ID:-}" ]]; then
  echo "ERROR: DISCORD_CLIENT_ID env var is not set." >&2
  echo "Add it in Render: Dashboard -> alpenglow-vgc -> Environment -> Add Environment Variable" >&2
  echo "(use the same Discord application client ID the mobile app uses)." >&2
  exit 1
fi

if [[ ! -x "$SDK_DIR/bin/flutter" ]]; then
  echo "Flutter SDK not found at $SDK_DIR - downloading stable..."
  STABLE_VERSION="$(python3 -c "
import json, urllib.request
d = json.load(urllib.request.urlopen('https://storage.googleapis.com/flutter_infra_release/releases/releases_linux.json', timeout=30))
print(d['current_release']['stable'])
" 2>/dev/null || echo "3.38.7")"
  echo "Installing Flutter $STABLE_VERSION ..."
  mkdir -p "$SDK_DIR"
  curl -fsSL -o /tmp/flutter.tar.xz \
    "https://storage.googleapis.com/flutter_infra_release/releases/stable/linux/flutter_linux_${STABLE_VERSION}-stable.tar.xz"
  tar -xf /tmp/flutter.tar.xz -C "$(dirname "$SDK_DIR")"
  # The tarball extracts a top-level `flutter/` dir; normalize to SDK_DIR.
  if [[ "$(dirname "$SDK_DIR")/flutter" != "$SDK_DIR" ]]; then
    rm -rf "$SDK_DIR"
    mv "$(dirname "$SDK_DIR")/flutter" "$SDK_DIR"
  fi
  rm -f /tmp/flutter.tar.xz
fi

export PATH="$SDK_DIR/bin:$PATH"
flutter --version

cd "$REPO_ROOT/app"
flutter build web --release \
  --dart-define=API_BASE_URL="https://alpenglow-vgc.onrender.com" \
  --dart-define=DISCORD_CLIENT_ID="$DISCORD_CLIENT_ID" \
  --dart-define=DISCORD_REDIRECT_URI="https://alpenglow-vgc.onrender.com/oauth/web-callback" \
  --dart-define=APP_URL_SCHEME="com.alpenglow.vgc.app"

# Keep the service worker from precaching ~6.3MB of hole maps on first load.
python3 "$REPO_ROOT/tools/strip_sw_precache.py" "$REPO_ROOT/app/build/web"

# Stage for FastAPI static serving (same origin as the API).
rm -rf "$REPO_ROOT/api/static_web"
cp -r "$REPO_ROOT/app/build/web" "$REPO_ROOT/api/static_web"
echo "PWA staged at $REPO_ROOT/api/static_web"

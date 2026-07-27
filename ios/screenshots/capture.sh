#!/usr/bin/env bash
#
# Capture the App Store screenshot set.
#
#   make ios-screenshots
#
# Screenshots have to be regenerated every time the UI moves, so this stages
# them from scratch each run rather than asking anyone to remember how they were
# taken: it starts a throwaway API on SQLite, seeds it with the demo household,
# creates a dedicated simulator, freezes the status bar at the Apple-standard
# 9:41, drives the app through MealsUITests/ScreenshotTests.swift, and collects
# the PNGs.
#
# Nothing here touches your own simulators or your real data — the device is
# created and destroyed by this script, and the database is a temp file.
#
# Options (environment variables):
#   DEVICES   space-separated simulator device *types* to shoot. The default is
#             the only size the App Store actually requires (6.9").
#   KEEP_SIM  set to 1 to leave the simulator behind for poking at.
#   PORT      port for the throwaway API (default 8123).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
IOS_DIR="$REPO_ROOT/ios/Meals"
OUT_DIR="$REPO_ROOT/ios/screenshots"

PORT="${PORT:-8123}"
SERVER_URL="http://127.0.0.1:$PORT"
# 6.9" (1320 x 2868) is the one required iPhone size — App Store Connect scales
# it down for smaller devices. Add types here only if you want the smaller sets
# to be shot for real rather than scaled.
DEVICES="${DEVICES:-iPhone-17-Pro-Max}"
SIM_NAME_PREFIX="Meals Screenshots"

EMAIL="demo@example.com"
PASSWORD="demo-password-123"

WORK_DIR="$(mktemp -d)"
API_PID=""
CREATED_SIMS=()

cleanup() {
  local status=$?
  [[ -n "$API_PID" ]] && kill "$API_PID" 2>/dev/null || true
  if [[ "${KEEP_SIM:-0}" != "1" ]]; then
    for udid in "${CREATED_SIMS[@]:-}"; do
      [[ -n "$udid" ]] || continue
      xcrun simctl shutdown "$udid" 2>/dev/null || true
      xcrun simctl delete "$udid" 2>/dev/null || true
    done
  fi
  rm -rf "$WORK_DIR"
  exit $status
}
trap cleanup EXIT INT TERM

say() { printf '\n\033[1m▸ %s\033[0m\n' "$*"; }

# ------------------------------------------------------------------ the API

# If something else already holds the port, the app would sign into *that* and
# the screenshots would quietly show somebody's real data. This has happened.
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Port $PORT is already in use — that's usually a dev stack." >&2
  echo "Stop it, or re-run with PORT=<free port>. The port must also match" >&2
  echo "ScreenshotTests.defaultServerURL, which is what the app falls back to." >&2
  exit 1
fi

say "Starting a throwaway API on $SERVER_URL"
export DATABASE_URL="sqlite+aiosqlite:///$WORK_DIR/screenshots.db"
cd "$REPO_ROOT/backend"
uv run alembic upgrade head >"$WORK_DIR/alembic.log" 2>&1 ||
  { echo "migrations failed:"; cat "$WORK_DIR/alembic.log"; exit 1; }
uv run uvicorn app.main:app --host 127.0.0.1 --port "$PORT" >"$WORK_DIR/api.log" 2>&1 &
API_PID=$!

for _ in $(seq 1 40); do
  curl -fsS "$SERVER_URL/healthz" >/dev/null 2>&1 && break
  sleep 0.5
done
curl -fsS "$SERVER_URL/healthz" >/dev/null ||
  { echo "the API never came up:"; cat "$WORK_DIR/api.log"; exit 1; }

say "Seeding the demo household"
SEED_API_URL="$SERVER_URL" uv run python -m app.seed >"$WORK_DIR/seed.log" 2>&1 ||
  { echo "seeding failed:"; cat "$WORK_DIR/seed.log"; exit 1; }

# The seeded plan has nothing cooked and no shopping-list history, which is
# accurate but flat. Mark one meal cooked so the plan shows the counters a real
# household would have by week two.
TOKEN="$(curl -fsS -X POST "$SERVER_URL/auth/login" -H 'Content-Type: application/json' \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])')"
read -r PLAN_ID PLAN_MEAL <<<"$(curl -fsS "$SERVER_URL/plans/current" -H "Authorization: Bearer $TOKEN" |
  python3 -c 'import json,sys; p=json.load(sys.stdin); m=p.get("meals") or [{}]; print(p.get("id",""), m[0].get("id",""))')"
if [[ -n "$PLAN_ID" && -n "$PLAN_MEAL" ]]; then
  curl -fsS -X POST "$SERVER_URL/plans/$PLAN_ID/meals/$PLAN_MEAL/cooked" \
    -H "Authorization: Bearer $TOKEN" >/dev/null || true
fi

# ------------------------------------------------------------------ the app

cd "$IOS_DIR"
say "Generating the Xcode project"
xcodegen generate >/dev/null

RUNTIME="$(xcrun simctl list runtimes -j |
  python3 -c 'import json,sys; rs=[r for r in json.load(sys.stdin)["runtimes"] if r["isAvailable"] and "iOS" in r["name"]]; print(sorted(rs, key=lambda r: r["version"])[-1]["identifier"])')"

for device_type in $DEVICES; do
  sim_name="$SIM_NAME_PREFIX $device_type"
  # A dedicated device, so a run can erase freely without touching simulators
  # you actually use — and so a stale keychain token can't skip the sign-in step.
  xcrun simctl delete "$sim_name" 2>/dev/null || true
  udid="$(xcrun simctl create "$sim_name" "com.apple.CoreSimulator.SimDeviceType.$device_type" "$RUNTIME")"
  CREATED_SIMS+=("$udid")

  say "Booting $device_type"
  xcrun simctl boot "$udid"
  xcrun simctl bootstatus "$udid" -b >/dev/null

  # The status bar is in every screenshot. Apple's own marketing time, full
  # bars, no carrier — anything else dates the shot or looks like a fault.
  xcrun simctl status_bar "$udid" override \
    --time "9:41" --dataNetwork wifi --wifiMode active --wifiBars 3 \
    --cellularMode active --cellularBars 4 --batteryState charged --batteryLevel 100

  say "Driving the app"
  set +e
  xcodebuild test \
    -project Meals.xcodeproj -scheme MealsScreenshots \
    -destination "id=$udid" -derivedDataPath build \
    -resultBundlePath "$WORK_DIR/$device_type.xcresult" \
    TEST_RUNNER_SCREENSHOT_SERVER_URL="$SERVER_URL" \
    TEST_RUNNER_SCREENSHOT_EMAIL="$EMAIL" \
    TEST_RUNNER_SCREENSHOT_PASSWORD="$PASSWORD" \
    >"$WORK_DIR/$device_type.log" 2>&1
  test_status=$?
  set -e
  if [[ $test_status -ne 0 ]]; then
    grep -E "error:|XCTAssert|Failing tests|failed" "$WORK_DIR/$device_type.log" | head -20
    echo "full log: $WORK_DIR/$device_type.log (kept only if you're quick — it's a temp dir)"
    cp "$WORK_DIR/$device_type.log" "$OUT_DIR/last-failure.log" 2>/dev/null || true
    exit $test_status
  fi

  # The runner wrote the PNGs into its own Documents directory, which is a real
  # directory on this Mac. Screenshots are also in the .xcresult, but pulling
  # them out of there needs a tool whose interface changes every Xcode release.
  container="$(xcrun simctl get_app_container "$udid" com.marcuslab.meals.uitests.xctrunner data)"
  destination="$OUT_DIR/$device_type"
  rm -rf "$destination"
  mkdir -p "$destination"
  cp "$container/Documents/"*.png "$destination/"

  say "$device_type"
  for shot in "$destination"/*.png; do
    dimensions="$(sips -g pixelWidth -g pixelHeight "$shot" | awk '/pixelWidth/ {w=$2} /pixelHeight/ {h=$2} END {print w "x" h}')"
    printf '   %-11s %s\n' "$dimensions" "$(basename "$shot")"
  done
done

say "Done — $OUT_DIR"
echo "Upload these in App Store Connect under the 6.9\" iPhone size class."

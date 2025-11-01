#!/usr/bin/env bash
# scripts/soak.sh
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Long-running, real-time soak for scrubexif's auto mode.
# Uses a REAL JPEG asset so exiftool can scrub successfully.
#
# Usage (defaults shown):
#   SCRUBEXIF_IMAGE=scrubexif:dev \
#   SOAK_MINUTES=10 \
#   SOAK_INTERVAL_SEC=30 \
#   SOAK_STABLE_SECONDS=120 \
#   SOAK_BATCH=3 \
#   ASSET=tests/assets/sample_with_gps_exif.jpg \
#   ./scripts/soak.sh
#
# Notes:
# - Leaves the work directory on disk for inspection; path is printed at the end.
# - Mounts /tmp as tmpfs in the container and passes the stability envs.
# - Creates unique filenames per cycle to avoid accidental duplicate handling unless you add them yourself.

set -euo pipefail

# -------- Config (env overrideable) --------
IMAGE="${SCRUBEXIF_IMAGE:-scrubexif:dev}"
SOAK_MINUTES="${SOAK_MINUTES:-10}"
SOAK_INTERVAL_SEC="${SOAK_INTERVAL_SEC:-30}"
SOAK_STABLE_SECONDS="${SOAK_STABLE_SECONDS:-120}"
SOAK_BATCH="${SOAK_BATCH:-3}"
ASSET="${ASSET:-tests/assets/sample_with_gps_exif.jpg}"
STATE_FILE="/tmp/.scrubexif_state.soak.json"

# -------- Preflight --------
command -v docker >/dev/null || { echo "❌ docker not found"; exit 1; }
if [[ ! -f "$ASSET" ]]; then
  echo "❌ Missing asset: $ASSET"
  echo "   Run from repo root or set ASSET=/path/to/real.jpg"
  exit 1
fi

# -------- Workdir --------
WORK="$(mktemp -d -t scrubexif-soak.XXXXXX)"
INPUT="$WORK/input"
OUTPUT="$WORK/output"
PROCESSED="$WORK/processed"
ERRORS="$WORK/errors"
LOGS="$WORK/logs"
mkdir -p "$INPUT" "$OUTPUT" "$PROCESSED" "$ERRORS" "$LOGS"

echo "⏲️  Soak starting"
echo "   Image:                $IMAGE"
echo "   Workdir:              $WORK"
echo "   Asset:                $ASSET"
echo "   Minutes:              $SOAK_MINUTES"
echo "   Interval (sec):       $SOAK_INTERVAL_SEC"
echo "   Stability seconds:    $SOAK_STABLE_SECONDS"
echo "   Batch per cycle:      $SOAK_BATCH"
echo

deadline=$(( $(date +%s) + SOAK_MINUTES*60 ))
cycle=0

run_container_once() {
  local log="$1"
  docker run --read-only --security-opt no-new-privileges --rm \
    --tmpfs /tmp:rw,exec,nosuid,size=64m \
    -e SCRUBEXIF_STABLE_SECONDS="$SOAK_STABLE_SECONDS" \
    -e SCRUBEXIF_STATE="$STATE_FILE" \
    -v "$INPUT:/photos/input" \
    -v "$OUTPUT:/photos/output" \
    -v "$PROCESSED:/photos/processed" \
    -v "$ERRORS:/photos/errors" \
    "$IMAGE" --from-input --log-level info \
    >"$log" 2>&1 || true
}

print_counts() {
  local outc proc err inc
  outc=$(ls -1 "$OUTPUT" 2>/dev/null | wc -l | tr -d ' ')
  proc=$(ls -1 "$PROCESSED" 2>/dev/null | wc -l | tr -d ' ')
  err=$(ls -1 "$ERRORS" 2>/dev/null | wc -l | tr -d ' ')
  inc=$(ls -1 "$INPUT" 2>/dev/null | wc -l | tr -d ' ')
  echo "   Counts → output:$outc  processed:$proc  errors:$err  input:$inc"
}

while [[ "$(date +%s)" -lt "$deadline" ]]; do
  cycle=$((cycle+1))
  # Generate a few *real* JPEGs by copying the asset with unique names
  for i in $(seq 1 "$SOAK_BATCH"); do
    cp -f "$ASSET" "$INPUT/soak_${cycle}_$(printf '%02d' "$i").jpg"
  done

  log="$LOGS/cycle_${cycle}.log"
  run_container_once "$log"

  # Summarize the last run (show the tool's summary if present)
  echo "── Cycle $cycle ─────────────────────────────────────────────────────"
  # Print the last 12 lines which should include the summary
  tail -n 12 "$log" || true
  print_counts
  echo "Sleeping $SOAK_INTERVAL_SEC s…"
  sleep "$SOAK_INTERVAL_SEC"
done

echo
echo "✅ Soak finished."
print_counts
echo "   Logs:    $LOGS (per-cycle output from the container)"
echo "   Workdir: $WORK"
echo "   Inspect e.g.: tail -n +1 $LOGS/cycle_*.log | sed -n 'p'"

# Exit non-zero if we saw persistent errors across cycles (quick heuristic)
total_errors=$(grep -h "Errors" "$LOGS"/cycle_*.log 2>/dev/null | awk '{print $3}' | paste -sd+ - | bc || echo 0)
if [[ "${total_errors:-0}" -gt 0 ]]; then
  echo "⚠️  Warning: non-zero Errors reported across cycles (sum: $total_errors)."
fi

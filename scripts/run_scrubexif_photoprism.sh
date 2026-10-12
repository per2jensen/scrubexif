#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Example integration script:
# - runs scrubexif in auto mode (--from-input)
# - parses SCRUBEXIF_SUMMARY from stdout
# - only triggers PhotoPrism index when new audited outputs were published
#
# Place this in scripts/run_scrubexif_photoprism.sh (or similar)
# and adjust INPUT_DIR / OUTPUT_DIR / PROCESSED_DIR for your setup.

set -euo pipefail

parse_schema_two_summary() {
  local summary_line="${1:-}"

  [[ "${summary_line}" == "SCRUBEXIF_SUMMARY schema=2 "* ]] || return 2
  awk '
    BEGIN {
      field_names = "schema total jpeg_total scrubbed duplicates_handled unsupported planned previewed skipped failed errors verified_duplicates duplicates_deleted duplicates_moved duplicates_unchanged duplicates_missing published run_errors interrupted examined duration"
      count_names = "total jpeg_total scrubbed duplicates_handled unsupported planned previewed skipped failed errors verified_duplicates duplicates_deleted duplicates_moved duplicates_unchanged duplicates_missing published run_errors interrupted examined"
      field_count = split(field_names, fields, " ")
      count_count = split(count_names, counts, " ")
      for (i = 1; i <= field_count; i++) required[fields[i]] = 1
    }
    {
      if ($1 != "SCRUBEXIF_SUMMARY" || $2 != "schema=2") exit 1
      for (i = 2; i <= NF; i++) {
        if (split($i, pair, "=") != 2) exit 1
        if (!(pair[1] in required) || seen[pair[1]]++) exit 1
        value[pair[1]] = pair[2]
      }
      for (name in required) if (!(name in seen)) exit 1
      for (i = 1; i <= count_count; i++) {
        name = counts[i]
        if (value[name] !~ /^[0-9][0-9]*$/) exit 1
      }
      if (value["duration"] !~ /^[0-9][0-9]*([.][0-9][0-9]*)?$/) exit 1
      if (value["total"] != value["jpeg_total"]) exit 1
      terminal = value["scrubbed"] + value["duplicates_handled"] + value["planned"] + value["previewed"] + value["skipped"] + value["failed"]
      if (value["jpeg_total"] != terminal) exit 1
      if (value["examined"] != terminal + value["unsupported"]) exit 1
      duplicate_facts = value["duplicates_deleted"] + value["duplicates_moved"] + value["duplicates_unchanged"] + value["duplicates_missing"]
      if (value["verified_duplicates"] != duplicate_facts) exit 1
      if (value["duplicates_handled"] > value["verified_duplicates"]) exit 1
      if (value["errors"] != value["failed"] + value["run_errors"]) exit 1
      if (value["published"] > value["scrubbed"] + value["failed"]) exit 1
      if (value["interrupted"] != 0 && value["interrupted"] != 1) exit 1
      if (value["interrupted"] == 1 && value["run_errors"] == 0) exit 1
      print value["published"], value["errors"], value["interrupted"]
    }
  ' <<< "${summary_line}"
}

# ------------------------------------------------------------
# Config: adjust these for your environment
# ------------------------------------------------------------

# scrubexif image (matches DETAILS.md pattern: VERSION variable)
SCRUBEXIF_VERSION="${SCRUBEXIF_VERSION:-0.8.0}"
SCRUBEXIF_IMAGE="${SCRUBEXIF_IMAGE:-per2jensen/scrubexif:${SCRUBEXIF_VERSION}}"

# Photoprism container name used with `docker exec`
PHOTOPRISM_CONTAINER="${PHOTOPRISM_CONTAINER:-photoprism}"

# Host paths for your workflow
# - INPUT_DIR: where new JPEGs arrive (e.g. from Photosync / rsync / rclone)
# - OUTPUT_DIR: scrubbed JPEGs for PhotoPrism to index
# - PROCESSED_DIR: originals moved here after scrub
INPUT_DIR="${INPUT_DIR:-/srv/photosync/input/sooc}"
OUTPUT_DIR="${OUTPUT_DIR:-/srv/photosync/output/scrubbed}"
PROCESSED_DIR="${PROCESSED_DIR:-/srv/photosync/processed}"

# Optional: stability window in seconds (see SCRUBEXIF_STABLE_SECONDS in DETAILS.md)
SCRUBEXIF_STABLE_SECONDS="${SCRUBEXIF_STABLE_SECONDS:-120}"

# Lock file to avoid overlapping runs (systemd timer / cron safe)
LOCKFILE="${LOCKFILE:-/tmp/scrubexif.lock}"

# ------------------------------------------------------------
# Basic sanity checks
# ------------------------------------------------------------

for d in "$INPUT_DIR" "$OUTPUT_DIR" "$PROCESSED_DIR"; do
  if [ ! -d "$d" ]; then
    echo "❌ Directory does not exist: $d" >&2
    exit 1
  fi
done

# ------------------------------------------------------------
# Locking: prevent overlapping runs
# ------------------------------------------------------------

if [ -e "$LOCKFILE" ]; then
  echo "🔁 scrubexif is already running (lock: $LOCKFILE)"
  exit 0
fi

trap 'rm -f "$LOCKFILE"' EXIT
touch "$LOCKFILE"

# ------------------------------------------------------------
# Run scrubexif (auto mode) and capture all output
# ------------------------------------------------------------

echo "🚀 Running scrubexif in auto mode from '$INPUT_DIR' -> '$OUTPUT_DIR'"

set +e
scrub_output="$(
  docker run --rm \
    --read-only --security-opt no-new-privileges \
    --tmpfs /tmp \
    -e SCRUBEXIF_STABLE_SECONDS="${SCRUBEXIF_STABLE_SECONDS}" \
    -v "${INPUT_DIR}:/photos/input" \
    -v "${OUTPUT_DIR}:/photos/output" \
    -v "${PROCESSED_DIR}:/photos/processed" \
    --user "$(id -u):$(id -g)" \
    "${SCRUBEXIF_IMAGE}" \
    --from-input \
    2>&1
)"
scrub_rc=$?
set -e

# Always emit scrubexif output into the journal / logs
echo "$scrub_output"

if [ "$scrub_rc" -ne 0 ] && [ "$scrub_rc" -ne 1 ]; then
  echo "❌ scrubexif container failed with rc=$scrub_rc, skipping PhotoPrism index"
  exit "$scrub_rc"
fi

# ------------------------------------------------------------
# Parse SCRUBEXIF_SUMMARY line from stdout
# ------------------------------------------------------------

mapfile -t summary_lines < <(printf '%s\n' "$scrub_output" | grep '^SCRUBEXIF_SUMMARY ' || true)
if [ "${#summary_lines[@]}" -ne 1 ]; then
  echo "❌ Expected exactly one SCRUBEXIF_SUMMARY line, got ${#summary_lines[@]}" >&2
  exit 1
fi
summary_line="${summary_lines[0]}"
if [[ "$summary_line" != "SCRUBEXIF_SUMMARY schema=2 "* ]]; then
  echo "❌ Unsupported scrubexif summary schema; expected schema=2" >&2
  exit 1
fi
if ! parsed_summary="$(parse_schema_two_summary "$summary_line")"; then
  echo "❌ Invalid or inconsistent schema=2 scrubexif summary" >&2
  exit 1
fi
read -r published errors interrupted <<< "$parsed_summary"

if [ "$scrub_rc" -eq 0 ] && { [ "$errors" -ne 0 ] || [ "$interrupted" -ne 0 ]; }; then
  echo "❌ Inconsistent scrubexif exit status and summary" >&2
  exit 1
fi
if [ "$scrub_rc" -eq 1 ] && { [ "$errors" -eq 0 ] || [ "$interrupted" -ne 0 ]; }; then
  echo "❌ Inconsistent scrubexif exit status and summary" >&2
  exit 1
fi

if [ "$published" -eq 0 ]; then
  echo "ℹ️ No newly published files (published=0), skipping PhotoPrism index"
  exit "$scrub_rc"
fi

if [ "$errors" -gt 0 ]; then
  echo "⚠️ Published $published files but also encountered $errors errors; still running PhotoPrism index"
fi

# ------------------------------------------------------------
# Trigger PhotoPrism import
# ------------------------------------------------------------

echo "📥 Triggering PhotoPrism import for $published newly published files..."
docker exec "$PHOTOPRISM_CONTAINER" photoprism index
exit "$scrub_rc"


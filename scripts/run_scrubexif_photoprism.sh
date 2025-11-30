#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Example integration script:
# - runs scrubexif in auto mode (--from-input)
# - parses SCRUBEXIF_SUMMARY from stdout
# - only triggers PhotoPrism index when new files were scrubbed
#
# Place this in scripts/run_scrubexif_photoprism.sh (or similar)
# and adjust INPUT_DIR / OUTPUT_DIR / PROCESSED_DIR for your setup.

set -euo pipefail

# ------------------------------------------------------------
# Config: adjust these for your environment
# ------------------------------------------------------------

# scrubexif image (matches DETAILS.md pattern: VERSION variable)
SCRUBEXIF_VERSION="${SCRUBEXIF_VERSION:-0.7.9}"
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

# Always emit scrubexif output into the journal / logs
echo "$scrub_output"

if [ "$scrub_rc" -ne 0 ]; then
  echo "❌ scrubexif container failed with rc=$scrub_rc, skipping PhotoPrism index"
  exit "$scrub_rc"
fi

# ------------------------------------------------------------
# Parse SCRUBEXIF_SUMMARY line from stdout
# ------------------------------------------------------------

summary_line="$(
  printf '%s\n' "$scrub_output" | awk '
    /^SCRUBEXIF_SUMMARY / { line=$0 }
    END { if (length(line) > 0) print line }
  '
)"

if [ -z "$summary_line" ]; then
  echo "⚠️ SCRUBEXIF_SUMMARY line not found in scrubexif output, skipping PhotoPrism index"
  exit 0
fi

# Extract scrubbed= and errors= fields
scrubbed="$(
  printf '%s\n' "$summary_line" | awk '
    {
      for (i = 1; i <= NF; i++) {
        if ($i ~ /^scrubbed=/) {
          split($i, a, "="); print a[2]; exit
        }
      }
    }
  '
)"

errors="$(
  printf '%s\n' "$summary_line" | awk '
    {
      for (i = 1; i <= NF; i++) {
        if ($i ~ /^errors=/) {
          split($i, a, "="); print a[2]; exit
        }
      }
    }
  '
)"

# Defensive defaults
scrubbed="${scrubbed:-0}"
errors="${errors:-0}"

# Ensure they are numeric
if ! [[ "$scrubbed" =~ ^[0-9]+$ ]]; then
  echo "⚠️ Could not parse scrubbed= value ('$scrubbed'), skipping PhotoPrism index"
  exit 0
fi

if ! [[ "$errors" =~ ^[0-9]+$ ]]; then
  echo "⚠️ Could not parse errors= value ('$errors'), skipping PhotoPrism index"
  exit 0
fi

if [ "$scrubbed" -eq 0 ]; then
  echo "ℹ️ No newly scrubbed files (scrubbed=0), skipping PhotoPrism index"
  exit 0
fi

if [ "$scrubbed" -gt 0 ] && [ "$errors" -gt 0 ]; then
  echo "⚠️ Some files scrubbed ($scrubbed) but also errors ($errors); still running PhotoPrism index"
fi

# ------------------------------------------------------------
# Trigger PhotoPrism import
# ------------------------------------------------------------

echo "📥 Triggering PhotoPrism import for $scrubbed newly scrubbed files..."
docker exec "$PHOTOPRISM_CONTAINER" photoprism index


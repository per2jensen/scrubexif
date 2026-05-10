#! /bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# License
# All scripts are licensed under the
# GNU Public License v3.0 or later.
# See license details here: https://www.gnu.org/licenses/gpl-3.0.html
#
# Demo script for `scrubexif` — scrubs all JPEGs in a directory and writes
# cleaned copies to a separate output directory (non-destructive).
#
# As -o option to `scrubexif` is used to specify the output directory,
# repeated runs of the script will overwrite the output directory.
#
# The Docker image is developed here: https://github.com/per2jensen/scrubexif
#
# Usage:
#   ./scrubexif-demo.sh <originals directory>  <scrubbed output directory> 
#
# Both the originals and scrubbed output directories must exist before running the script.
#
# Scrubbed JPEGs are written to <scrubbed output directory>/
# The originals in <originals directory>/ are left untouched.

LOGFILE=/tmp/scrubexif.log
MAX_LOG=102400   # 100k
HALF_LOG=51200   #  50k

log() {
    echo "$*" | tee -a "$LOGFILE"
}

abort() {
    log "$0: ERROR: $*"
    notify-send -u critical "scrubexif ❌" "$*"
    exit 1
}

# Trim the log file if it exceeds the max size, keeping only the last half
if [ -f "$LOGFILE" ] && [ "$(stat -c%s "$LOGFILE")" -gt "$MAX_LOG" ]; then
    tail -c $HALF_LOG "$LOGFILE" | tail -n +2 > "$LOGFILE.tmp" && mv "$LOGFILE.tmp" "$LOGFILE"
fi

log "===>>> $(date --iso-8601=seconds) - running $0"

# Guard: directory argument required
PHOTO_DIR="${1}"
[[ -z "$PHOTO_DIR" ]] && abort "No directory supplied. Usage: $0  <originals directory>  <scrubbed output directory> "
[[ -d "$PHOTO_DIR" ]] || abort "Directory not found: $PHOTO_DIR"

# Guard: directory argument required
OUTPUT_DIR="${2}"
[[ -z "$OUTPUT_DIR" ]] && abort "No directory supplied. Usage: $0  <originals directory>  <scrubbed output directory>"
[[ -d "$OUTPUT_DIR" ]] || abort "Directory not found: $OUTPUT_DIR"



# Resolve to absolute path
PHOTO_DIR=$(realpath "$PHOTO_DIR")
OUTPUT_DIR=$(realpath "$OUTPUT_DIR")

log "Input:  $PHOTO_DIR"
log "Output: $OUTPUT_DIR"

# Check docker is available
command -v docker &>/dev/null || abort "docker is not installed or not in PATH"
docker info &>/dev/null       || abort "docker daemon is not running or current user cannot reach it"

# UID/GID handling — run the container as the current user
RUN_AS_UID=${RUN_AS_UID:-$(id -u)}
RUN_AS_GID=${RUN_AS_GID:-$(id -g)}

if [ "$RUN_AS_UID" -eq 0 ]; then
    abort "Running as root is not allowed"
fi

log "Running as UID=$RUN_AS_UID GID=$RUN_AS_GID"

docker run --rm \
    --user "$RUN_AS_UID:$RUN_AS_GID" \
    --read-only --security-opt no-new-privileges \
    --tmpfs /tmp \
    -v "$PHOTO_DIR:/photos" \
    -v "$OUTPUT_DIR:/scrubbed" \
    per2jensen/scrubexif:latest \
    -o /scrubbed | tee -a "$LOGFILE"

# Parse the SCRUBEXIF_SUMMARY line
SUMMARY_LINE=$(grep "SCRUBEXIF_SUMMARY" "$LOGFILE" | tail -1)
SCRUBBED=$(echo "$SUMMARY_LINE" | grep -oP 'scrubbed=\K[0-9]+')
SKIPPED=$(echo "$SUMMARY_LINE"  | grep -oP 'skipped=\K[0-9]+')
ERRORS=$(echo "$SUMMARY_LINE"   | grep -oP 'errors=\K[0-9]+')
TOTAL=$(echo "$SUMMARY_LINE"    | grep -oP 'total=\K[0-9]+')

# Check we actually got a summary — if not, docker or scrubexif likely failed
if [[ -z "$TOTAL" ]]; then
    abort "No summary line found — scrubexif may have failed. Check log: $LOGFILE"
fi

if [[ "$ERRORS" -gt 0 || "$SKIPPED" -gt 0 ]]; then
    notify-send -u critical "scrubexif ❌" "Scrubbed $SCRUBBED/$TOTAL files — skipped: $SKIPPED, errors: $ERRORS"
else
    notify-send "scrubexif ✅" "Scrubbed $SCRUBBED/$TOTAL files successfully — output: $OUTPUT_DIR"
fi

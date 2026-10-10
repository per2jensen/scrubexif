#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Demo wrapper for non-destructive JPEG scrubbing with desktop notifications.
# Usage: ./scrubexif-demo.sh <originals directory> <scrubbed output directory>

set -euo pipefail

LOGFILE="${SCRUBEXIF_LOGFILE:-/tmp/scrubexif.log}"
MAX_LOG=102400
HALF_LOG=51200
RUN_LOG=""

log() {
    echo "$*" | tee -a "${LOGFILE}"
}

cleanup() {
    if [[ -n "${RUN_LOG}" && -f "${RUN_LOG}" ]]; then
        rm -f "${RUN_LOG}"
    fi
}

abort() {
    local message="${1:-Unknown failure}"

    echo "$0: ERROR: ${message}" | tee -a "${LOGFILE}" >&2
    if ! notify-send -u critical -i dialog-error "scrubexif ❌" "${message}"; then
        echo "$0: WARNING: desktop notification failed" | tee -a "${LOGFILE}" >&2
    fi
    exit 1
}

summary_value() {
    local summary_line="${1:-}"
    local key="${2:-}"

    if [[ -z "${summary_line}" || -z "${key}" ]]; then
        return 1
    fi

    printf '%s\n' "${summary_line}" | awk -v wanted="${key}" '
        {
            for (i = 1; i <= NF; i++) {
                if ($i ~ ("^" wanted "=")) {
                    split($i, value, "=")
                    print value[2]
                    exit
                }
            }
        }
    '
}

validate_count() {
    local name="${1:-}"
    local value="${2:-}"

    if [[ -z "${name}" || ! "${value}" =~ ^[0-9]+$ ]]; then
        abort "Invalid ${name} count in scrubexif summary: '${value}'"
    fi
}

send_summary_notification() {
    local total="${1}"
    local scrubbed="${2}"
    local skipped="${3}"
    local errors="${4}"
    local unsupported="${5}"
    local examined="${6}"
    local duplicates_deleted="${7}"
    local duplicates_moved="${8}"
    local output_dir="${9}"
    local duplicates_handled=$((duplicates_deleted + duplicates_moved))
    local body=""
    local duplicate_detail=""

    if ((duplicates_handled > 0)); then
        duplicate_detail="; duplicates: ${duplicates_handled}"
    fi

    if ((errors > 0)); then
        body="Examined ${examined} files — scrubbed ${scrubbed}/${total} JPEGs; skipped: ${skipped}, errors: ${errors}${duplicate_detail}; unsupported: ${unsupported}"
        notify-send -u critical -i dialog-error "scrubexif ❌ Completed with errors" "${body}"
        return
    fi

    if ((skipped > 0)); then
        body="Examined ${examined} files — scrubbed ${scrubbed}/${total} JPEGs; skipped: ${skipped}${duplicate_detail}; unsupported: ${unsupported}"
        notify-send -u normal -i dialog-warning "scrubexif ⚠️ Completed with warnings" "${body}"
        return
    fi

    if ((total == 0)); then
        if ((unsupported == 1)); then
            body="Nothing processed — 1 unsupported file examined"
        elif ((unsupported > 1)); then
            body="Nothing processed — ${unsupported} unsupported files examined"
        else
            body="Nothing to do — no files examined"
        fi
        notify-send -u low -i dialog-information "scrubexif ℹ️" "${body}"
        return
    fi

    if ((scrubbed == 0 && duplicates_handled > 0)); then
        if ((duplicates_handled == 1)); then
            body="No new output — 1 duplicate handled; examined: ${examined}"
        else
            body="No new output — ${duplicates_handled} duplicates handled; examined: ${examined}"
        fi
        notify-send -u low -i dialog-information "scrubexif ℹ️" "${body}"
        return
    fi

    if ((scrubbed == 0)); then
        body="Completed — no files changed; examined: ${examined}"
        notify-send -u low -i dialog-information "scrubexif ℹ️" "${body}"
        return
    fi

    body="Examined ${examined} files — scrubbed ${scrubbed}/${total} JPEGs${duplicate_detail}; unsupported: ${unsupported}; output: ${output_dir}"
    notify-send -u normal -i emblem-default "scrubexif ✅ Scrubbing complete" "${body}"
}

rotate_log_if_needed() {
    local rotated_log=""

    if [[ ! -f "${LOGFILE}" ]] || (( $(stat -c%s "${LOGFILE}") <= MAX_LOG )); then
        return
    fi

    rotated_log="$(mktemp --tmpdir="$(dirname "${LOGFILE}")" scrubexif-log.XXXXXX)"
    if ! tail -c "${HALF_LOG}" "${LOGFILE}" | tail -n +2 > "${rotated_log}"; then
        rm -f "${rotated_log}"
        abort "Unable to rotate log: ${LOGFILE}"
    fi
    mv "${rotated_log}" "${LOGFILE}"
}

main() {
    local photo_dir="${1:-}"
    local output_dir="${2:-}"
    local run_as_uid="${RUN_AS_UID:-$(id -u)}"
    local run_as_gid="${RUN_AS_GID:-$(id -g)}"
    local docker_status=0
    local summary_line=""
    local total=""
    local scrubbed=""
    local skipped=""
    local errors=""
    local unsupported=""
    local examined=""
    local duplicates_deleted=""
    local duplicates_moved=""

    trap cleanup EXIT
    rotate_log_if_needed
    log "===>>> $(date --iso-8601=seconds) - running $0"

    [[ -n "${photo_dir}" ]] || abort "No directory supplied. Usage: $0 <originals directory> <scrubbed output directory>"
    [[ -d "${photo_dir}" ]] || abort "Directory not found: ${photo_dir}"
    [[ -n "${output_dir}" ]] || abort "No output directory supplied. Usage: $0 <originals directory> <scrubbed output directory>"
    [[ -d "${output_dir}" ]] || abort "Directory not found: ${output_dir}"

    photo_dir="$(realpath "${photo_dir}")"
    output_dir="$(realpath "${output_dir}")"
    log "Input:  ${photo_dir}"
    log "Output: ${output_dir}"

    command -v docker &>/dev/null || abort "docker is not installed or not in PATH"
    docker info &>/dev/null || abort "docker daemon is not running or current user cannot reach it"
    if ((run_as_uid == 0)); then
        abort "Running as root is not allowed"
    fi

    log "Running as UID=${run_as_uid} GID=${run_as_gid}"
    RUN_LOG="$(mktemp --tmpdir scrubexif-run.XXXXXX)"

    set +e
    docker run --rm \
        --user "${run_as_uid}:${run_as_gid}" \
        --read-only --security-opt no-new-privileges \
        --tmpfs /tmp \
        -v "${photo_dir}:/photos" \
        -v "${output_dir}:/scrubbed" \
        per2jensen/scrubexif:latest \
        -o /scrubbed 2>&1 | tee -a "${LOGFILE}" "${RUN_LOG}"
    docker_status="${PIPESTATUS[0]}"
    set -e

    summary_line="$(awk '/^SCRUBEXIF_SUMMARY / { line=$0 } END { print line }' "${RUN_LOG}")"
    [[ -n "${summary_line}" ]] || abort "No summary line found — scrubexif may have failed. Check log: ${LOGFILE}"

    total="$(summary_value "${summary_line}" total)"
    scrubbed="$(summary_value "${summary_line}" scrubbed)"
    skipped="$(summary_value "${summary_line}" skipped)"
    errors="$(summary_value "${summary_line}" errors)"
    duplicates_deleted="$(summary_value "${summary_line}" duplicates_deleted)"
    duplicates_moved="$(summary_value "${summary_line}" duplicates_moved)"
    unsupported="$(summary_value "${summary_line}" unsupported)"
    examined="$(summary_value "${summary_line}" examined)"

    unsupported="${unsupported:-0}"
    validate_count total "${total}"
    validate_count scrubbed "${scrubbed}"
    validate_count skipped "${skipped}"
    validate_count errors "${errors}"
    validate_count duplicates_deleted "${duplicates_deleted}"
    validate_count duplicates_moved "${duplicates_moved}"
    validate_count unsupported "${unsupported}"
    examined="${examined:-$((total + unsupported))}"
    validate_count examined "${examined}"

    send_summary_notification \
        "${total}" "${scrubbed}" "${skipped}" "${errors}" \
        "${unsupported}" "${examined}" "${duplicates_deleted}" \
        "${duplicates_moved}" "${output_dir}"

    if ((docker_status != 0)); then
        exit "${docker_status}"
    fi
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi

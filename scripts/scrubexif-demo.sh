#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Demo wrapper for non-destructive JPEG scrubbing with desktop notifications.
# Usage: ./scrubexif-demo.sh <originals directory> <scrubbed output directory>

set -euo pipefail

LOGFILE="${SCRUBEXIF_LOGFILE:-/tmp/scrubexif.log}"
SCRUBEXIF_IMAGE="${SCRUBEXIF_IMAGE:-per2jensen/scrubexif:0.8.0}"
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
            print value["total"], value["scrubbed"], value["skipped"], value["errors"], value["unsupported"], value["examined"], value["duplicates_handled"], value["interrupted"]
        }
    ' <<< "${summary_line}"
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
    local duplicates_handled="${7}"
    local output_dir="${8}"
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
    local summary_line="" parsed_summary=""
    local total=""
    local scrubbed=""
    local skipped=""
    local errors=""
    local unsupported=""
    local examined=""
    local duplicates_handled=""
    local interrupted=""
    local -a summary_lines=()

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
        "${SCRUBEXIF_IMAGE}" \
        -o /scrubbed 2>&1 | tee -a "${LOGFILE}" "${RUN_LOG}"
    docker_status="${PIPESTATUS[0]}"
    set -e

    mapfile -t summary_lines < <(grep '^SCRUBEXIF_SUMMARY ' "${RUN_LOG}" || true)
    ((${#summary_lines[@]} == 1)) || abort "Expected exactly one SCRUBEXIF_SUMMARY line, got ${#summary_lines[@]}. Check log: ${LOGFILE}"
    summary_line="${summary_lines[0]}"
    if [[ "${summary_line}" != "SCRUBEXIF_SUMMARY schema=2 "* ]]; then
        abort "Unsupported scrubexif summary schema; expected schema=2"
    fi
    if ! parsed_summary="$(parse_schema_two_summary "${summary_line}")"; then
        abort "Invalid or inconsistent schema=2 scrubexif summary"
    fi
    read -r total scrubbed skipped errors unsupported examined duplicates_handled interrupted <<< "${parsed_summary}"

    if ((docker_status == 0 && (errors != 0 || interrupted != 0))); then
        abort "Inconsistent scrubexif exit status and schema=2 summary"
    fi
    if ((docker_status == 1 && (errors == 0 || interrupted != 0))); then
        abort "Inconsistent scrubexif exit status and schema=2 summary"
    fi
    if ((docker_status == 130 && interrupted != 1)); then
        abort "Inconsistent scrubexif interruption status and schema=2 summary"
    fi

    send_summary_notification \
        "${total}" "${scrubbed}" "${skipped}" "${errors}" \
        "${unsupported}" "${examined}" "${duplicates_handled}" "${output_dir}"

    if ((docker_status != 0)); then
        exit "${docker_status}"
    fi
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi

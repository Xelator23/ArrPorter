#!/usr/bin/env bash
# Run on the Unraid HOST. Default is check-only; no background daemon.
set -Eeuo pipefail
umask 077
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
MODE=${1:---check}
[[ "$MODE" == --check || "$MODE" == --apply ]] || { echo 'Use --check or --apply'; exit 2; }
# These are non-secret deployment settings, unrelated to uppollo.conf.
SONARR_CONTAINER=${SONARR_CONTAINER:-Sonarr}
SONARR_SCRIPT_DIR=${SONARR_SCRIPT_DIR:-/scripts/arrporter/upload_cleanup/sonarr}
UPPOLLO_LOG_DIR=${UPPOLLO_LOG_DIR:-/mnt/user/appdata/uppollo/logs}
log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*"; }
for cmd in docker jq awk date flock mktemp; do
    command -v "$cmd" >/dev/null || { log "ERROR: Programm fehlt auf dem Host: $cmd"; exit 1; }
done
exec 9>"$HERE/.auto-cleanup.lock"
flock -n 9 || exit 0
TMP=$(mktemp -d)
trap 'rm -f -- "$TMP/events" "$TMP/states" "$TMP/marker"; rmdir -- "$TMP" 2>/dev/null || true' EXIT
# Freeze a conservative upper time bound before reading live logs.
NOW=$(date +%s)
shopt -s nullglob
export LC_ALL=C
LOGS=("$UPPOLLO_LOG_DIR"/upPollo_api_[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].log)
[[ ${#LOGS[@]} -gt 0 ]] || { log 'ERROR: Keine datierten upPollo-Logs gefunden.'; exit 1; }
# Shell expansion sorts ISO dates; parse files together to preserve midnight jobs.
awk -f "$HERE/uppollo-completions.awk" "${LOGS[@]}" > "$TMP/events"
docker exec -i "$SONARR_CONTAINER" bash -s -- "$SONARR_SCRIPT_DIR" > "$TMP/states" <<'CONTAINER'
set -Eeuo pipefail
source "$1/cleanup.conf"
shopt -s nullglob
files=("$TRANSIT_BASE/_state/"*.json)
for file in "${files[@]}"; do
    [[ ! -L "$file" ]] || continue
    job=${file##*/}; job=${job%.json}
    [[ "$job" =~ ^[a-f0-9]{64}$ ]] || continue
    jq -c --arg job "$job" 'select(.status == "accepted_unverified" or
      (.status == "cleanup_started" and .transitKind == "file"))
      | select((.submittedAt // "") != "")
      | {job:$job,path:.path,submittedAt:.submittedAt,updatedAt:.updatedAt}' "$file"
done
CONTAINER
FOUND=0
ERRORS=0
while IFS= read -r state; do
    [[ -n "$state" ]] || continue
    job=$(jq -er .job <<< "$state")
    path=$(jq -er .path <<< "$state")
    submitted=$(date -d "$(jq -er .submittedAt <<< "$state")" +%s)
    accepted=$(date -d "$(jq -er .updatedAt <<< "$state")" +%s)
    matches=0
    while IFS=$'\t' read -r start finish event_path; do
        [[ "$event_path" == "$path" ]] || continue
        start_epoch=$(date -d "$start" +%s) || continue
        finish_epoch=$(date -d "$finish" +%s) || continue
        # Correlate this submission, not a successful upload from a previous day.
        # A queued start outside this window stays pending for manual inspection.
        if ((start_epoch >= submitted && start_epoch <= accepted + 5 && finish_epoch >= start_epoch && finish_epoch < NOW)); then
            ((matches+=1))
        fi
    done < "$TMP/events"
    if ((matches != 1)); then
        log "PENDING: $job — kein eindeutiger Upload-Abschluss; Dupe-Prüfung folgt."
        continue
    fi
    ((FOUND+=1))
    log "LOG CONFIRMED: $job — $path"
    cleanup_mode=--check
    [[ "$MODE" != --apply ]] || cleanup_mode=--apply-confirmed
    # No shell interpolation of job IDs, file paths or container names.
    if ! docker exec -e CLEANUP_CONFIRMATION=uppollo_log "$SONARR_CONTAINER" bash \
        "$SONARR_SCRIPT_DIR/cleanup-confirmed.sh" "$cleanup_mode" "$job"; then
        log "ERROR: Cleanup-Prüfung fehlgeschlagen für $job; siehe Ausgabe."
        ((ERRORS+=1))
    fi
done < "$TMP/states"
# A dupe is a separate terminal outcome, never a successful upload. Pass the
# complete dated log stream to the container; no new log mount is required.
if ! cat -- "${LOGS[@]}" | docker exec -i "$SONARR_CONTAINER" bash \
    "$SONARR_SCRIPT_DIR/cleanup-duplicates.sh" "$MODE" --before "$NOW"; then
    log 'ERROR: Dupe-Prüfung fehlgeschlagen; siehe Ausgabe.'
    ((ERRORS+=1))
fi
log "Fertig: $FOUND bestätigte Upload-Abschlüsse; $ERRORS Fehler; Modus=$MODE"
((ERRORS == 0))

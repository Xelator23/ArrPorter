#!/usr/bin/env bash
# Targeted cleanup of a job whose upPollo completion was independently confirmed.
# Default: checks only. This is NOT a scheduler or an upPollo completion detector.
set -Eeuo pipefail
umask 077
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$HERE/../../scripts/load-config.sh" "$HERE" cleanup
export SPECIAL_GROUP
MODE=${1:---check}
JOB=${2:-}
[[ "$MODE" == --check || "$MODE" == --apply-confirmed ]] || { echo 'Use --check or --apply-confirmed JOB_ID'; exit 2; }
[[ "$JOB" =~ ^[a-f0-9]{64}$ ]] || { echo 'A complete 64-character job ID is required.'; exit 2; }
log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*"; }
die() { log "ERROR: $*" >&2; exit 1; }
for cmd in curl jq flock realpath stat cmp find sort sha256sum mktemp; do
    command -v "$cmd" >/dev/null || die "Abhängigkeit fehlt: $cmd"
done
for key in TRANSIT_BASE QBIT_URL QBIT_USER QBIT_PASS; do
    [[ -n ${!key:-} ]] || die "Konfiguration fehlt: $key"
done
BASE=$(realpath -e -- "$TRANSIT_BASE")
STATE="$BASE/_state/$JOB.json"
[[ -f "$STATE" && ! -L "$STATE" ]] || die 'Statusdatei fehlt oder ist ein Symlink.'
KIND=$(jq -er '.transitKind // "directory"' "$STATE")
[[ "$KIND" == directory || "$KIND" == file ]] || die 'Unbekannter Transittyp.'
if [[ "$KIND" == file ]]; then
    export TRANSIT_BASE QBIT_URL QBIT_USER QBIT_PASS CLIENT_UPLOAD_BASE CLIENT_SPECIAL_BASE CLEANUP_CONFIRMATION
    exec python3 -B "$HERE/cleanup_single_file.py" "$MODE" "$JOB"
fi
exec 9>"$BASE/_state/$JOB.lock"
flock -n 9 || die 'Import wird gerade verarbeitet.'
STATUS=$(jq -er .status "$STATE")
[[ "$STATUS" != cleaned ]] || { log 'Bereits bereinigt.'; exit 0; }
[[ "$STATUS" == accepted_unverified || "$STATUS" == cleanup_started ]] || die "Status erlaubt keinen Cleanup: $STATUS"
RELEASE=$(jq -er '.release | select(type == "string" and length > 0)' "$STATE")
[[ "$RELEASE" != .* && "$RELEASE" != */* && "$RELEASE" != *\\* && ! "$RELEASE" =~ [[:cntrl:]] ]] || die 'Unsicherer Release-Name.'
if [[ -n ${SPECIAL_GROUP:-} && "${RELEASE^^}" == *-"${SPECIAL_GROUP^^}" ]]; then
    TARGET_CONFIG=${CLIENT_SPECIAL_BASE:-/data/torrents/special}
else
    TARGET_CONFIG=${CLIENT_UPLOAD_BASE:-/data/torrents/upload}
fi
[[ "$TARGET_CONFIG" == /* ]] || die 'Zielbasis muss ein absoluter Pfad sein.'
TARGET_BASE=$(realpath -e -- "$TARGET_CONFIG")
[[ "$BASE" != / && "$TARGET_BASE" != / && "$BASE" != "$TARGET_BASE" && "$BASE/" != "$TARGET_BASE/"* && "$TARGET_BASE/" != "$BASE/"* ]] || die 'Unsichere/überlappende Basisordner.'
log "Zielbasis für $RELEASE: $TARGET_BASE"
TRANSIT_FOLDER=$(jq -er '.transitFolder // .release | select(type == "string" and length > 0)' "$STATE")
[[ "$TRANSIT_FOLDER" != .* && "$TRANSIT_FOLDER" != */* && "$TRANSIT_FOLDER" != *\\* && ! "$TRANSIT_FOLDER" =~ [[:cntrl:]] ]] || die 'Unsicherer Transitordnername.'
DEST="$BASE/$TRANSIT_FOLDER"
TARGET="$TARGET_BASE/$TRANSIT_FOLDER"
[[ $(jq -er .path "$STATE") == "$DEST" ]] || die 'Statuspfad stimmt nicht mit Transit überein.'
exec 8>"$BASE/_state/$(printf %s "$TRANSIT_FOLDER" | sha256sum | cut -d' ' -f1).release.lock"
flock -n 8 || die 'Release wird gerade verarbeitet.'
[[ -d "$DEST" && ! -L "$DEST" && $(realpath -e -- "$DEST") == "$DEST" ]] || die 'Transitordner fehlt oder wird umgeleitet.'
[[ -d "$TARGET" && ! -L "$TARGET" && $(realpath -e -- "$TARGET") == "$TARGET" ]] || die 'Zielordner fehlt oder wird umgeleitet.'
TMP=$(mktemp -d)
trap 'rm -f -- "$TMP/cookie" "$TMP/login" "$TMP/info" "$TMP/files" "$TMP/list" "$TMP/dirs" "$TMP/special"; rmdir -- "$TMP" 2>/dev/null || true' EXIT
http() { curl -q --silent --show-error --fail --connect-timeout 10 --max-time 30 "$@"; }
http --cookie-jar "$TMP/cookie" --output "$TMP/login" \
    --data-urlencode "username=$QBIT_USER" --data-urlencode "password=$QBIT_PASS" "$QBIT_URL/api/v2/auth/login"
[[ $(cat "$TMP/login") == 'Ok.' ]] || die 'qBittorrent-Login fehlgeschlagen.'
# A single-file torrent may report the file itself as content_path.
# Derive that exact path from the transit, never from a name/prefix guess.
find "$DEST" -mindepth 1 ! -type f ! -type d -print0 > "$TMP/special"
[[ ! -s "$TMP/special" ]] || die 'Transit enthält Symlinks oder spezielle Dateien.'
find "$DEST" -type f -print0 > "$TMP/list"
mapfile -d '' FILES < "$TMP/list"
[[ ${#FILES[@]} -gt 0 ]] || die 'Transit enthält keine prüfbaren Dateien.'
SINGLE_TARGET=''
if [[ ${#FILES[@]} == 1 ]]; then
    SINGLE_TARGET="$TARGET/${FILES[0]#"$DEST/"}"
fi
refresh() {
    http --cookie "$TMP/cookie" --output "$TMP/info" "$QBIT_URL/api/v2/torrents/info"
    # Any client entry referencing the transit blocks deletion, including another torrent.
    jq -e --arg source "$DEST" 'type == "array" and all(.[];
      ((.content_path // "") | (. != $source and (startswith($source + "/") | not))) and
      ((.save_path // "") | (. != $source and (startswith($source + "/") | not))))' "$TMP/info" >/dev/null || die 'Ein Torrent verwendet noch den Transit oder die API-Antwort ist ungültig.'
    TORRENT_HASH=$(jq -er --arg target "$TARGET" --arg single "$SINGLE_TARGET" '
      [.[] | select(.content_path == $target or
        ($single != "" and .content_path == $single))]
      | if length != 1 then error("Keine eindeutige Torrent-Zuordnung") else .[0] end
      | select(.progress == 1 and .amount_left == 0)
      | select(.state == "uploading" or .state == "stalledUP" or .state == "forcedUP" or
               .state == "queuedUP" or .state == "pausedUP" or .state == "stoppedUP")
      | .hash' "$TMP/info") || die 'Zieltorrent fehlt, ist nicht vollständig oder wird geprüft/verschoben.'
    [[ "$TORRENT_HASH" =~ ^([a-fA-F0-9]{40}|[a-fA-F0-9]{64})$ ]] || die 'Ungültiger Ziel-Hash.'
    http --cookie "$TMP/cookie" --get --data-urlencode "hash=$TORRENT_HASH" --output "$TMP/files" "$QBIT_URL/api/v2/torrents/files"
    jq -e 'type == "array" and length > 0 and all(.[];
      .progress == 1 and (.name | type == "string") and (.size | type == "number"))' "$TMP/files" >/dev/null || die 'Nicht alle Torrent-Dateien sind vollständig.'
}
refresh
INITIAL_HASH=$TORRENT_HASH
verify_file() {
    local file=$1 rel target size
    rel=${file#"$DEST/"}; target="$TARGET/$rel"
    [[ -f "$file" && ! -L "$file" && $(realpath -e -- "$file") == "$file" ]] || die 'Transitdatei wurde verändert/umgeleitet.'
    [[ -f "$target" && ! -L "$target" && $(realpath -e -- "$target") == "$target" ]] || die "Zieldatei fehlt/ist umgeleitet: $rel"
    size=$(stat -c %s -- "$file")
    [[ "$size" == "$(stat -c %s -- "$target")" ]] || die "Dateigröße weicht ab: $rel"
    jq -e --arg rel "$rel" --arg prefixed "$TRANSIT_FOLDER/$rel" --argjson size "$size" '
      any(.[]; (.name == $rel or .name == $prefixed) and .size == $size and .progress == 1)' "$TMP/files" >/dev/null || die "Datei nicht vollständig im Zieltorrent nachgewiesen: $rel"
    if [[ ! "$file" -ef "$target" ]]; then
        cmp -s -- "$file" "$target" || die "Dateiinhalt weicht ab: $rel"
    fi
}
for file in "${FILES[@]}"; do verify_file "$file"; done
log "CHECK OK: ${#FILES[@]} Transitdateien am Ziel verifiziert. Torrent=$TORRENT_HASH; Ziel=$TARGET"
if [[ "$MODE" == --check ]]; then
    log 'Nichts gelöscht. Anwenden nur nach unabhängig bestätigtem Abschluss von upPollo.'
    exit 0
fi
refresh
[[ "$TORRENT_HASH" == "$INITIAL_HASH" ]] || die 'Torrent-Zuordnung hat sich geändert.'
set_status() {
    jq --arg status "$1" --arg hash "$TORRENT_HASH" --arg time "$(date -u +%FT%TZ)" \
      --arg confirmation "${CLEANUP_CONFIRMATION:-operator}" \
      '.status=$status | .targetTorrentHash=$hash | .cleanupUpdatedAt=$time | .completionConfirmation=$confirmation' "$STATE" > "$STATE.cleanup.tmp"
    mv -- "$STATE.cleanup.tmp" "$STATE"
}
set_status cleanup_started
for file in "${FILES[@]}"; do
    verify_file "$file"
    rm -- "$file"
done
# rmdir only removes empty directories; unexpected new content is preserved.
find "$DEST" -depth -type d -print0 > "$TMP/dirs"
while IFS= read -r -d '' dir; do rmdir -- "$dir"; done < "$TMP/dirs"
set_status cleaned
log "CLEANED: Nur der verifizierte Transitordner wurde entfernt: $DEST"

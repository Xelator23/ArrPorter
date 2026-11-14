#!/usr/bin/env bash
# Radarr -> upPollo 2.0.5. Requires Bash 4.4+, curl, jq and GNU coreutils.
set -Eeuo pipefail
umask 077
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
MODE=${1:-run}
case "$MODE" in run|--check|--dry-run) ;; *) echo 'Usage: radarr-uppollo.sh [--check|--dry-run]' >&2; exit 2;; esac
log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*"; }
die() { log "ERROR: $*" >&2; exit 1; }
skip() { log "SKIP: $*"; exit 0; }
source "$SCRIPT_DIR/../scripts/load-config.sh" "$SCRIPT_DIR"
export SPECIAL_GROUP
source "${FILTER_CONFIG:-$SCRIPT_DIR/filters.conf}"
for cmd in python3 curl jq flock realpath sha256sum stat find sort ln mktemp; do
    command -v "$cmd" >/dev/null || die "Abhängigkeit fehlt: $cmd"
done
for key in UPPOLLO_URL API_KEY TRANSIT_BASE; do
    [[ -n "${!key:-}" ]] || die "Konfiguration unvollständig: $key"
done
[[ "$TRANSIT_BASE" = /* && "$TRANSIT_BASE" != / ]] || die 'TRANSIT_BASE muss ein absoluter Unterordner sein.'
[[ ${DRY_RUN:-true} == true || ${DRY_RUN:-true} == false ]] || die 'DRY_RUN muss true oder false sein.'
if [[ "$MODE" == --check || ${radarr_eventtype:-} == Test ]]; then
    UPPOLLO_URL="$UPPOLLO_URL" API_KEY="$API_KEY" python3 "$SCRIPT_DIR/uppollo_connection_test.py"
    exit 0
fi
[[ ${radarr_eventtype:-} == Download ]] || skip "Ereignis ${radarr_eventtype:-unbekannt} wird ignoriert."
[[ "$MODE" == --dry-run ]] && DRY_RUN=true
DRY_RUN=${DRY_RUN:-true}
HASH=${radarr_download_id:-}; HASH=${HASH,,}
[[ -n "$HASH" && ! "$HASH" =~ [[:cntrl:]] ]] || die 'Download-ID fehlt oder ist ungültig.'
CLIENT_TYPE=${radarr_download_client_type:-}; CLIENT_TYPE=${CLIENT_TYPE,,}
case "$CLIENT_TYPE" in
    sabnzbd|nzbget) SOURCE_KIND=usenet;;
    'usenet blackhole'|usenetblackhole) SOURCE_KIND=blackhole;;
    qbittorrent)
        SOURCE_KIND=torrent
        [[ "$HASH" =~ ^([a-f0-9]{40}|[a-f0-9]{64})$ ]] || die 'Gültiger Torrent-Hash fehlt.'
        for key in QBIT_URL QBIT_USER QBIT_PASS; do
            [[ -n "${!key:-}" ]] || die "Konfiguration unvollständig: $key"
        done
        ;;
    *) die "Download-Client-Typ fehlt oder wird nicht unterstützt: ${CLIENT_TYPE:-unbekannt}";;
esac
[[ -f ${radarr_moviefile_path:-} && ! -L ${radarr_moviefile_path:-} ]] || die 'Importierte Videodatei fehlt oder ist ein Symlink.'
VIDEO=$(realpath -e -- "$radarr_moviefile_path")
BASE=$(realpath -e -- "${radarr_movie_path:?Filmordner fehlt}")
[[ "$VIDEO" == "$BASE/"* ]] || die 'Importdatei liegt außerhalb des Filmordners.'
case "${VIDEO,,}" in *.mkv|*.mp4) ;; *) die 'Importdatei ist keine MKV/MP4.';; esac
[[ -s "$VIDEO" ]] || die 'Importdatei ist leer.'
# SourcePath describes the file BEFORE Radarr's import/rename. It need not exist
# anymore: use only its name and hardlink the verified imported library file.
ORIGINAL_PATH=${radarr_moviefile_sourcepath:-}
[[ -n "$ORIGINAL_PATH" && "$ORIGINAL_PATH" != */ ]] || die 'Ursprünglicher Videopfad fehlt; Originalnamen nicht sicher rekonstruierbar.'
VIDEO_NAME=${ORIGINAL_PATH##*/}
[[ "$VIDEO_NAME" != .* && "$VIDEO_NAME" != *\\* && ! "$VIDEO_NAME" =~ [[:cntrl:]] ]] || die 'Unsicherer Originaldateiname.'
ORIGINAL_EXT=${VIDEO_NAME##*.}; IMPORT_EXT=${VIDEO##*.}
[[ ${ORIGINAL_EXT,,} == ${IMPORT_EXT,,} ]] || die 'Dateiendung von Original und Import widerspricht sich.'
TRANSIT_BASE=$(realpath -m -- "$TRANSIT_BASE")
[[ "$TRANSIT_BASE" != "$BASE" && "$BASE/" != "$TRANSIT_BASE/"* && "$TRANSIT_BASE/" != "$BASE/"* ]] || die 'Transit und Filmordner dürfen sich nicht überlappen.'
mkdir -p -- "$TRANSIT_BASE/_state" "$TRANSIT_BASE/_logs"
JOB=$(printf '%s\n%s\n' "$HASH" "$VIDEO" | sha256sum); JOB=${JOB%% *}
exec 9>"$TRANSIT_BASE/_state/$JOB.lock"
flock -n 9 || skip 'Dieser Import wird bereits verarbeitet.'
exec >>"$TRANSIT_BASE/_logs/$JOB.log" 2>&1
STATE="$TRANSIT_BASE/_state/$JOB.json"
TMP=$(mktemp -d)
cleanup() { rm -f -- "$TMP/cookie" "$TMP/login" "$TMP/request.json" "$TMP/response" "$TMP/trackers" "$TMP/plan.json"; rmdir -- "$TMP" 2>/dev/null || true; }
trap cleanup EXIT
trap 'log "ERROR: Abbruch in Zeile $LINENO. Transit bleibt erhalten; siehe Statusdatei."' ERR
log "START: Modus=$DRY_RUN (true=Test), Job=$JOB"
if [[ -f "$STATE" ]]; then
    log "Vorhandener Status: $(jq -r .status "$STATE")"
    skip "Vorgang bereits angelegt. Vor erneutem Upload Status prüfen: $STATE"
fi
# Disable ~/.curlrc, redirects and automatic retries; a POST must never be repeated blindly.
http() { curl -q --silent --show-error --connect-timeout 10 --max-time 30 --fail "$@"; }
log "Quelle: $SOURCE_KIND; Client-Typ: $CLIENT_TYPE"
if [[ "$SOURCE_KIND" == torrent ]]; then
http --cookie-jar "$TMP/cookie" --output "$TMP/login" \
    --data-urlencode "username=$QBIT_USER" --data-urlencode "password=$QBIT_PASS" \
    "$QBIT_URL/api/v2/auth/login"
[[ $(cat "$TMP/login") == 'Ok.' ]] || die 'qBittorrent hat den Login nicht bestätigt.'
http --cookie "$TMP/cookie" --get --data-urlencode "hash=$HASH" \
    --output "$TMP/trackers" "$QBIT_URL/api/v2/torrents/trackers"
jq -e 'type == "array" and all(.[]; (.url | type == "string"))' "$TMP/trackers" >/dev/null || die 'Ungültige Tracker-Antwort.'
TRACKERS=$(jq -r '.[].url | select(test("^(https?|udp|wss?)://"; "i"))' "$TMP/trackers")
[[ -n "$TRACKERS" ]] || die 'Keine echten Tracker gefunden; Übergabe gestoppt.'
for blocked in "${EXCLUDED_INDEXERS[@]}"; do
    [[ ${TRACKERS,,} != *"${blocked,,}"* ]] || skip "Gesperrter Tracker: $blocked"
done
elif [[ "$SOURCE_KIND" == blackhole ]]; then
    log 'Blackhole: Ursprüngliche Downloadseite unbekannt; Quellenfilter entfällt. Keine qBittorrent-Quellabfrage erforderlich.'
else
    INDEXER=${radarr_release_indexer:-}
    if [[ -z "$INDEXER" ]]; then
        [[ -n ${RADARR_URL:-} && -n ${RADARR_API_KEY:-} && ${radarr_movie_id:-} =~ ^[0-9]+$ ]] || die 'Usenet-Indexer fehlt; Radarr-Historie nicht konfiguriert.'
        HISTORY=$(http --header "X-Api-Key: $RADARR_API_KEY" --get --data-urlencode "movieId=$radarr_movie_id" "$RADARR_URL/api/v3/history/movie")
        INDEXER=$(jq -er --arg hash "$HASH" '
          [.[] | select(((.downloadId // "") | ascii_downcase) == $hash)
           | .data.indexer | select(type == "string" and length > 0)] | unique
          | if length == 1 then .[0] else error("Kein eindeutiger Usenet-Indexer") end' <<<"$HISTORY")
    fi
    for blocked in "${EXCLUDED_INDEXERS[@]}"; do
        [[ ${INDEXER,,} != *"${blocked,,}"* ]] || skip "Gesperrter Usenet-Indexer: $blocked"
    done
    log "Usenet-Indexer geprüft: $INDEXER. Keine qBittorrent-Quellabfrage erforderlich."
fi
RELEASE=${radarr_moviefile_scenename:-${radarr_release_title:-${radarr_download_client_release_title:-}}}
if [[ -z "$RELEASE" && "$SOURCE_KIND" == blackhole ]]; then
    SOURCE_ROOT=$(realpath -e -- "${radarr_moviefile_sourcefolder:?Blackhole-Quellordner fehlt}")
    [[ "$SOURCE_ROOT" != / && "$ORIGINAL_PATH" == "$SOURCE_ROOT/"* && $(realpath -m -- "$ORIGINAL_PATH") == "$ORIGINAL_PATH" ]] || die 'Blackhole: Originalvideo liegt nicht eindeutig im Downloadordner.'
    RELEASE=${SOURCE_ROOT##*/}
fi
if [[ -z "$RELEASE" ]]; then
    [[ -n ${RADARR_URL:-} && -n ${RADARR_API_KEY:-} && ${radarr_movie_id:-} =~ ^[0-9]+$ ]] || die 'Scene-Name fehlt; Radarr-Historie nicht konfiguriert.'
    HISTORY=$(http --header "X-Api-Key: $RADARR_API_KEY" --get --data-urlencode "movieId=$radarr_movie_id" "$RADARR_URL/api/v3/history/movie")
    RELEASE=$(jq -er --arg hash "$HASH" '
      [.[] | select(((.downloadId // "") | ascii_downcase) == $hash)
       | .sourceTitle | select(type == "string" and length > 0)] | unique
      | if length == 1 then .[0] else error("Kein eindeutiger Release-Name") end' <<<"$HISTORY")
fi
[[ -n "$RELEASE" && "$RELEASE" != .* && "$RELEASE" != */* && "$RELEASE" != *\\* && ${#RELEASE} -le 200 && ! "$RELEASE" =~ [[:cntrl:]] ]] || die 'Unsicherer oder zu langer Release-Name.'
UPPER=${RELEASE^^}
for blocked in "${EXCLUDED_INDEXERS[@]}"; do
    [[ ${RELEASE,,} != *"${blocked,,}"* ]] || skip "Gesperrter Name: $blocked (kein Tracker-Nachweis)."
done
for group in "${EXCLUDED_GROUPS[@]}"; do
    [[ "$UPPER" != *"-${group^^}" ]] || skip "Gesperrte Gruppe: $group"
done
# Treat tags literally; regex-escape metacharacters such as '+' first.
CHECK="${radarr_moviefile_quality:-} $RELEASE $(basename -- "$VIDEO")"
matches() {
    jq -en --arg text "$CHECK" --arg tag "$1" '
      ($tag | explode | map(. as $c | if ([92,46,94,36,124,63,42,43,40,41,91,93,123,125] | index($c)) != null
        then [92,$c] else [$c] end) | flatten | implode) as $escaped
      | $text | test("(^|[-. _])" + $escaped + "([-. _]|$)"; "i")' >/dev/null
}
for tag in "${!ALLOWED_TAGS[@]}"; do
    if [[ ${ALLOWED_TAGS[$tag]} == false ]] && matches "$tag"; then skip "Gesperrter Tag: $tag"; fi
done
FOUND=false
for res in "${!ALLOWED_RESOLUTIONS[@]}"; do
    if matches "$res"; then
        [[ ${ALLOWED_RESOLUTIONS[$res]} == true ]] || skip "Gesperrte Auflösung: $res"
        FOUND=true
    fi
done
[[ "$FOUND" == true ]] || skip 'Keine erlaubte Auflösung erkannt.'
# Prefer a sidecar matching the imported video or release; never choose an arbitrary NFO.
NFO=''
[[ -f ${RADARR_EXTRAS_MANIFEST:-} && ! -L "$RADARR_EXTRAS_MANIFEST" ]] || die 'Aktuelle Extras-Liste fehlt; radarr-import-chain.sh verwenden.'
jq -e --arg base "$BASE" '.destination == $base and (.files | type == "array")' "$RADARR_EXTRAS_MANIFEST" >/dev/null || die 'Extras-Liste gehört nicht zum Filmordner.'
mapfile -t CURRENT_EXTRAS < <(jq -r '.files[].path' "$RADARR_EXTRAS_MANIFEST")
NFOS=()
for extra in "${CURRENT_EXTRAS[@]}"; do
    [[ "$extra" == "$BASE/"* && -f "$extra" && ! -L "$extra" && $(realpath -e -- "$extra") == "$extra" ]] || die 'Aktuelles Extra fehlt oder wird umgeleitet.'
    expected=$(jq -er --arg path "$extra" '.files[] | select(.path == $path) | .sha256' "$RADARR_EXTRAS_MANIFEST")
    actual=$(sha256sum -- "$extra"); actual=${actual%% *}
    [[ "$actual" == "$expected" ]] || die 'Aktuelles Extra wurde verändert.'
    [[ ${extra,,} != *.nfo ]] || NFOS+=("$extra")
done
for candidate in "${NFOS[@]}"; do
    stem=$(basename -- "$candidate"); stem=${stem%.*}
    video_stem=$(basename -- "$VIDEO"); video_stem=${video_stem%.*}
    if [[ ${stem,,} == ${video_stem,,} || ${stem,,} == ${RELEASE,,} ]]; then NFO=$candidate; break; fi
done
if [[ -z "$NFO" && ${#NFOS[@]} == 1 ]]; then NFO=${NFOS[0]}; fi
VALID_NFO=false
if [[ -n "$NFO" && -s "$NFO" ]]; then
    VALID_NFO=true
    if grep -qiE '^(General|Allgemein)[[:space:]]*$' "$NFO" &&
       grep -qE '^[A-Za-z ]+[ ]{10,}:' "$NFO" &&
       ! grep -qiE 'imdb\.com|themoviedb\.org|director|handlung' "$NFO"; then VALID_NFO=false; fi
fi
for group in "${NFO_REQUIRED_GROUPS[@]}"; do
    if [[ "$UPPER" == *"-${group^^}" && "$VALID_NFO" != true ]]; then skip "Keine eindeutige, brauchbare NFO für $group"; fi
done
IMDB=${radarr_movie_imdbid:-}
TMDB=${radarr_movie_tmdbid:-0}
[[ -z "$IMDB" || "$IMDB" =~ ^tt[0-9]{7,10}$ ]] || die 'Ungültige IMDb-ID.'
[[ "$TMDB" =~ ^(0|[1-9][0-9]*)$ ]] || die 'Ungültige TMDb-ID.'
# Do not silently replace authoritative import metadata with an arbitrary NFO ID.
if [[ "$VALID_NFO" == true ]]; then
    NFO_IMDB=$(grep -oE 'tt[0-9]{7,10}' "$NFO" | sort -u || true)
    [[ -z "$NFO_IMDB" || -z "$IMDB" || "$NFO_IMDB" == "$IMDB" ]] || die 'NFO-IMDb widerspricht Radarr oder enthält mehrere IDs.'
fi
[[ -n "$IMDB" || "$TMDB" != 0 ]] || die 'Keine Film-ID vorhanden.'
CATEGORY=null
if [[ -n ${SPECIAL_GROUP:-} && "$UPPER" == *-"${SPECIAL_GROUP^^}" ]]; then CATEGORY=$(jq -Rn --arg g "$SPECIAL_GROUP" '$g'); fi
SOURCE_FOLDER=${radarr_moviefile_sourcefolder:-}
[[ "$SOURCE_FOLDER" == /* ]] || die 'Originaler Downloadordner fehlt oder ist nicht absolut.'
while [[ "$SOURCE_FOLDER" == */ && "$SOURCE_FOLDER" != / ]]; do SOURCE_FOLDER=${SOURCE_FOLDER%/}; done
TRANSIT_FOLDER=${SOURCE_FOLDER##*/}
[[ -n "$TRANSIT_FOLDER" && "$TRANSIT_FOLDER" != .* && "$TRANSIT_FOLDER" != *\\* && ! "$TRANSIT_FOLDER" =~ [[:cntrl:]] ]] || die 'Unsicherer Originalordnername; wird nicht umbenannt oder ersetzt.'
# The job lock above and release lock remain held through the POST.
exec 8>"$TRANSIT_BASE/_state/$(printf %s "$TRANSIT_FOLDER" | sha256sum | cut -d' ' -f1).release.lock"
flock -n 8 || skip 'Release wird bereits vorbereitet.'
export TRANSIT_BASE RADARR_EXTRAS_MANIFEST
export CLIENT_UPLOAD_BASE="${CLIENT_UPLOAD_BASE:-/data/torrents/upload}"
export CLIENT_SPECIAL_BASE="${CLIENT_SPECIAL_BASE:-/data/torrents/special}"
export RT_BASE="$BASE" RT_VIDEO="$VIDEO" RT_VIDEO_NAME="$VIDEO_NAME"
export RT_FOLDER="$TRANSIT_FOLDER" RT_RELEASE="$RELEASE" RT_JOB="$JOB" RT_HASH="$HASH"
export RT_DRY_RUN="$DRY_RUN" RT_NFO=''
[[ "$VALID_NFO" != true ]] || export RT_NFO="$NFO"
python3 -B "$SCRIPT_DIR/radarr_transit.py" prepare > "$TMP/plan.json"
DEST=$(jq -er .path "$TMP/plan.json")
log "Prüfung OK: Release=$RELEASE; Video=$VIDEO; Kategorie=$CATEGORY; NFO=$VALID_NFO"
log "Transit: $(jq -r .transitKind "$TMP/plan.json"); Pfad=$DEST"
if [[ "$DRY_RUN" == true ]]; then
    log "DRY RUN OK: Kein Transit angelegt, kein Upload. Geplanter Pfad: $DEST"
    exit 0
fi
write_state() {
    jq --arg status "$1" --arg time "$(date -u +%FT%TZ)" --arg submittedAt "${SUBMITTED_AT:-}" \
      '.status=$status | .updatedAt=$time | .submittedAt=$submittedAt' "$STATE" > "$STATE.tmp"
    mv -- "$STATE.tmp" "$STATE"
}
python3 -B "$SCRIPT_DIR/radarr_transit.py" guard "$JOB"
jq -n --arg APIKey "$API_KEY" --arg Path "$DEST" --arg IMDBID "$IMDB" \
  --argjson TMDBID "$TMDB" --argjson Category "$CATEGORY" \
  '{APIKey:$APIKey,Path:$Path,IMDBID:$IMDBID,TMDBID:$TMDBID,Category:$Category}' >"$TMP/request.json"
# A durable marker is written BEFORE the POST: after a crash/time-out do not resend.
SUBMITTED_AT=$(date -u +%FT%TZ)
write_state submitting
if STATUS=$(curl -q --silent --show-error --connect-timeout 10 --max-time 600 \
    --request POST --header 'Content-Type: application/json' --data-binary "@$TMP/request.json" \
    --output "$TMP/response" --write-out '%{http_code}' "$UPPOLLO_URL"); then
    if [[ "$STATUS" == 2?? ]]; then
        write_state accepted_unverified
        log "HTTP $STATUS: Übergabe beantwortet. Verarbeitung NICHT bestätigt; Transit bleibt erhalten."
    else
        write_state http_error
        die "HTTP $STATUS von upPollo. Kein automatischer Wiederholungsversuch. Transit bleibt erhalten."
    fi
else
    write_state outcome_unknown
    die 'Netzwerkfehler/Timeout: Annahme unklar. Transit bleibt erhalten; vor Wiederholung upPollo prüfen.'
fi
log "Status: $STATE"
log 'Kein automatischer Cleanup ohne dokumentierte Abschlussbestätigung und verifizierten endgültigen Dateipfad.'

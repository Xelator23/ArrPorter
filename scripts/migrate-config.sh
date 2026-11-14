#!/usr/bin/env bash
# Run from any directory. Old configs are trusted Bash files.
set -Eeuo pipefail
shopt -s inherit_errexit
set +x
umask 077
root=$(cd -- "$(dirname -- "$0")/.." && pwd)
common_source=''
blackhole_default=''
if [[ ${1:-} == --common && ( $# == 2 || $# == 3 ) && -f ${2:-} ]]; then
    common_source=$(realpath -e -- "$2")
    blackhole_default=${3:-}
    set -- "$common_source" "$common_source"
elif [[ ( $# != 0 && $# != 2 ) || ${1:-} == --common ]]; then
    echo 'Usage: bash scripts/migrate-config.sh [radarr/uppollo.conf sonarr/uppollo.conf | --common common/uppollo.conf [blackhole-base]]' >&2
    exit 2
fi
dest="$root/arrporter.conf"
if [[ -e "$dest" || -L "$dest" ]]; then
    echo 'ERROR: arrporter.conf existiert bereits.' >&2
    exit 1
fi
shared=(UPPOLLO_URL API_KEY QBIT_URL QBIT_USER QBIT_PASS CLIENT_UPLOAD_BASE SPECIAL_GROUP CLIENT_SPECIAL_BASE)
declare -A radarr_values sonarr_values
read_settings() (
    local app=$1 file=$2 key
    shift 2
    SCRIPT_DIR=$(cd -- "$(dirname -- "$file")" && pwd)
    HERE=$SCRIPT_DIR
    COMMON_DIR=$SCRIPT_DIR
    source "$file" >/dev/null
    if [[ -n "$common_source" ]]; then
        if [[ "$app" == radarr ]]; then
            TRANSIT_BASE=${RADARR_TRANSIT_BASE:?RADARR_TRANSIT_BASE fehlt}
            DRY_RUN=${RADARR_DRY_RUN:-${DRY_RUN:-true}}
        else
            TRANSIT_BASE=${SONARR_TRANSIT_BASE:?SONARR_TRANSIT_BASE fehlt}
            DRY_RUN=${SONARR_DRY_RUN:-${DRY_RUN:-true}}
            BLACKHOLE_BASE=${BLACKHOLE_BASE:-$blackhole_default}
        fi
        if [[ -n ${CLIENT_XELAVIE_BASE:-} ]]; then
            if [[ ${SPECIAL_GROUP:-Xelavie} != Xelavie || ${CLIENT_SPECIAL_BASE:-$CLIENT_XELAVIE_BASE} != "$CLIENT_XELAVIE_BASE" ]]; then
                echo 'ERROR: Widersprüchliche Sondergruppe oder Zielpfade.' >&2
                exit 1
            fi
            SPECIAL_GROUP=Xelavie
            CLIENT_SPECIAL_BASE=$CLIENT_XELAVIE_BASE
        fi
        unset CLIENT_XELAVIE_BASE
    fi
    for key in ${!CLIENT_@}; do
        if [[ "$key" == CLIENT_*_BASE && "$key" != CLIENT_UPLOAD_BASE && "$key" != CLIENT_SPECIAL_BASE ]]; then
            printf 'ERROR: Bitte %s zuerst auf SPECIAL_GROUP und CLIENT_SPECIAL_BASE umstellen.\n' "$key" >&2
            exit 1
        fi
    done
    : "${TRANSIT_BASE:?TRANSIT_BASE fehlt}"
    DRY_RUN=${DRY_RUN:-true}
    CLIENT_UPLOAD_BASE=${CLIENT_UPLOAD_BASE:-/data/torrents/upload}
    CLIENT_SPECIAL_BASE=${CLIENT_SPECIAL_BASE:-/data/torrents/special}
    for key in "${shared[@]}" TRANSIT_BASE DRY_RUN "$@"; do
        printf '%s_values[%s]=%q\n' "$app" "$key" "${!key-}"
    done
)
# Only fixed keys and values escaped by Bash printf %q reach eval.
rows=$(read_settings radarr "${1:-$root/radarr/uppollo.conf}" RADARR_URL RADARR_API_KEY FILTER_CONFIG)
eval "$rows"
rows=$(read_settings sonarr "${2:-$root/sonarr/uppollo.conf}" SONARR_URL SONARR_API_KEY BLACKHOLE_BASE)
eval "$rows"
unset rows
for key in "${shared[@]}"; do
    if [[ ${radarr_values[$key]} != "${sonarr_values[$key]}" ]]; then
        echo "ERROR: Unterschiedliche Werte für $key. Vor der Migration lokal abgleichen." >&2
        exit 1
    fi
done
tmp=$(mktemp "$root/.arrporter.XXXXXXXX.conf")
trap 'rm -f -- "$tmp"' EXIT
{
    for key in "${shared[@]}" RADARR_URL RADARR_API_KEY; do printf '%s=%q\n' "$key" "${radarr_values[$key]}"; done
    for key in SONARR_URL SONARR_API_KEY BLACKHOLE_BASE; do printf '%s=%q\n' "$key" "${sonarr_values[$key]}"; done
    for key in TRANSIT_BASE DRY_RUN; do
        printf 'RADARR_%s=%q\n' "$key" "${radarr_values[$key]}"
        printf 'SONARR_%s=%q\n' "$key" "${sonarr_values[$key]}"
    done
    if [[ -n ${radarr_values[FILTER_CONFIG]} ]]; then
        printf 'RADARR_FILTER_CONFIG=%q\n' "${radarr_values[FILTER_CONFIG]}"
    fi
} > "$tmp"
for app in radarr sonarr; do
    # Check both mappings before making the file visible to a running importer.
    (source "$tmp"; source "$root/scripts/select-service.sh" "$app")
done
ln -- "$tmp" "$dest"
echo 'OK: Gemeinsame arrporter.conf angelegt. Bisherige Dateien und Filter bleiben erhalten.'

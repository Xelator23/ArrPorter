#!/usr/bin/env bash
# One-time local migration of six cleanup settings, without printing credentials.
# Does not upload, clean media, edit import scripts or replace existing configs.
set -Eeuo pipefail
umask 077
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
for cmd in docker flock mktemp; do command -v "$cmd" >/dev/null; done
exec 9>"$HERE/.cleanup.lock"
flock -n 9 || { echo 'ERROR: Cleanup läuft gerade.' >&2; exit 1; }
if [[ ! -e "$HERE/settings.conf" && ! -L "$HERE/settings.conf" ]]; then
    cp -n -- "$HERE/settings.conf.example" "$HERE/settings.conf"
    chmod 600 "$HERE/settings.conf"
fi
[[ -f "$HERE/settings.conf" && ! -L "$HERE/settings.conf" ]]
source "$HERE/settings.conf"
tmp=''
trap '[[ -z "$tmp" ]] || rm -f -- "$tmp"' EXIT
for app in radarr sonarr; do
    if [[ "$app" == radarr ]]; then
        container=$RADARR_CONTAINER
        old_dir=/scripts/arrporter/radarr
    else
        container=$SONARR_CONTAINER
        old_dir=/scripts/arrporter/sonarr
    fi
    dir="$HERE/$app"
    [[ -d "$dir" && ! -L "$dir" ]]
    docker exec "$container" test -f /scripts/arrporter/upload_cleanup/setup.sh
    docker exec "$container" test -f "/scripts/arrporter/upload_cleanup/$app/cleanup-confirmed.sh"
    if [[ -e "$HERE/../arrporter.conf" || -L "$HERE/../arrporter.conf" ]]; then
        docker exec "$container" bash -ec '
            test -r /scripts/arrporter/arrporter.conf
            source /scripts/arrporter/scripts/load-config.sh "/scripts/arrporter/upload_cleanup/$1" cleanup
            for key in TRANSIT_BASE QBIT_URL QBIT_USER QBIT_PASS; do
                [[ -n ${!key:-} ]] || { echo "ERROR: Konfigurationsfeld fehlt: $key" >&2; exit 1; }
            done
        ' bash "$app"
        printf '%s: gemeinsame arrporter.conf für den Cleanup geprüft.\n' "$app"
        continue
    fi
    config="$dir/cleanup.conf"
    if [[ -e "$config" || -L "$config" ]]; then
        [[ -f "$config" && ! -L "$config" ]] || { echo 'ERROR: Unsichere Konfigurationsdatei.' >&2; exit 1; }
        printf '%s: vorhandene eigene Cleanup-Konfiguration bleibt erhalten.\n' "$app"
        continue
    fi
    tmp=$(mktemp "$dir/.cleanup-config.XXXXXX")
    if ! docker exec -i "$container" bash -s -- "$old_dir/uppollo.conf" > "$tmp" <<'EXPORT_CONFIG'
set -Eeuo pipefail
set +x
source "$1" >/dev/null
for key in TRANSIT_BASE QBIT_URL QBIT_USER QBIT_PASS; do
    [[ -n ${!key:-} ]] || { printf 'ERROR: Konfigurationsfeld fehlt: %s\n' "$key" >&2; exit 1; }
done
[[ "$TRANSIT_BASE" == /* && "$TRANSIT_BASE" != / ]]
CLIENT_UPLOAD_BASE=${CLIENT_UPLOAD_BASE:-/data/torrents/upload}
CLIENT_SPECIAL_BASE=${CLIENT_SPECIAL_BASE:-/data/torrents/special}
printf '# Eigene Cleanup-Konfiguration; keine Import-/upPollo-API-Schlüssel.\n'
SPECIAL_GROUP=${SPECIAL_GROUP:-}
for key in TRANSIT_BASE QBIT_URL QBIT_USER QBIT_PASS CLIENT_UPLOAD_BASE CLIENT_SPECIAL_BASE SPECIAL_GROUP; do
    printf '%s=%q\n' "$key" "${!key}"
done
EXPORT_CONFIG
    then
        printf 'ERROR: Konfiguration für %s konnte nicht übernommen werden.\n' "$app" >&2
        exit 1
    fi
    # Hardlink creates the config atomically and refuses any existing destination.
    chmod 600 "$tmp"
    ln -- "$tmp" "$config"
    rm -- "$tmp"
    tmp=''
    printf '%s: eigene Cleanup-Konfiguration angelegt; Zugangsdaten nicht ausgegeben.\n' "$app"
done
echo 'SETUP OK: Cleanup-Konfiguration für beide Dienste geprüft.'
echo 'Importskripte, Mediendateien und vorhandene Statusdateien wurden nicht verändert.'

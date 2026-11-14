#!/usr/bin/env bash
# Run mock-based tests in each actual container. No real cleanup or API calls.
set -Eeuo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$HERE/settings.conf"
for script in "$HERE"/*.sh "$HERE"/radarr/*.sh "$HERE"/sonarr/*.sh; do
    bash -n "$script"
done
for app in radarr sonarr; do
    if [[ "$app" == radarr ]]; then container=$RADARR_CONTAINER; else container=$SONARR_CONTAINER; fi
    docker exec -i "$container" bash -s -- "/scripts/arrporter/upload_cleanup/$app" <<'TEST'
set -Eeuo pipefail
cd -- "$1"
source ./cleanup.conf
for key in TRANSIT_BASE QBIT_URL QBIT_USER QBIT_PASS CLIENT_UPLOAD_BASE CLIENT_SPECIAL_BASE; do
    [[ -n ${!key:-} ]] || { printf 'ERROR: Konfigurationsfeld fehlt: %s\n' "$key" >&2; exit 1; }
done
[[ -d "$TRANSIT_BASE/_state" ]] || { echo 'ERROR: Konfigurierte Statusbasis fehlt.' >&2; exit 1; }
for cmd in python3 curl jq flock realpath stat cmp find sort sha256sum mktemp; do
    command -v "$cmd" >/dev/null
done
python3 -B -m unittest discover -v
TEST
done
echo 'TEST OK: Beide eigenständigen Cleanup-Bereiche geprüft; keine Mediendateien verändert.'

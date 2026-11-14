#!/usr/bin/env bash
# One-time state archival; never removes media or submits uploads.
set -Eeuo pipefail
umask 077
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
MODE=${1:---check}
[[ $# -le 1 && ( "$MODE" == --check || "$MODE" == --apply ) ]] || exit 2
source "$HERE/settings.conf"
exec 9>"$HERE/.cleanup.lock"
flock -n 9 || { echo 'ERROR: Cleanup läuft bereits.' >&2; exit 1; }
errors=0
for app in Radarr Sonarr; do
    if [[ "$app" == Radarr ]]; then container=$RADARR_CONTAINER; subdir=radarr; else container=$SONARR_CONTAINER; subdir=sonarr; fi
    if ! docker exec "$container" bash -c '
        set -Eeuo pipefail
        set -a
        source "/scripts/arrporter/upload_cleanup/$1/cleanup.conf"
        set +a
        exec python3 -B /scripts/arrporter/upload_cleanup/reconcile.py "$2" "$3"
    ' bash "$subdir" "$app" "$MODE"; then
        errors=$((errors + 1))
    fi
done
((errors == 0))

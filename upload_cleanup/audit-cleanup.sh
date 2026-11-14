#!/usr/bin/env bash
# Run on Unraid host. Read-only: state files, paths and qBittorrent info.
set -Eeuo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
failed=0
for app in Radarr Sonarr; do
    if [[ "$app" == Radarr ]]; then
        container=${RADARR_CONTAINER:-Radarr}
        script_dir=${RADARR_SCRIPT_DIR:-/scripts/arrporter/upload_cleanup/radarr}
    else
        container=${SONARR_CONTAINER:-Sonarr}
        script_dir=${SONARR_SCRIPT_DIR:-/scripts/arrporter/upload_cleanup/sonarr}
    fi
    printf '\n=== %s ===\n' "$container"
    if ! docker exec -i "$container" bash -c '
        set -Eeuo pipefail
        set -a
        source "$1/cleanup.conf"
        set +a
        export AUDIT_APP="$2"
        exec python3 -B -
    ' bash "$script_dir" "$app" < "$HERE/audit_cleanup.py"; then
        failed=1
    fi
done
exit "$failed"

#!/usr/bin/env bash
# Independent cleanup entry point, installed on the Unraid host.
set -Eeuo pipefail
umask 077
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
MODE=${1:---check}
if (($# > 1)) || [[ "$MODE" != --check && "$MODE" != --apply && "$MODE" != --diagnose ]]; then
    echo 'Aufruf: cleanup.sh [--check|--apply|--diagnose]' >&2
    exit 2
fi
[[ -f "$HERE/settings.conf" && ! -L "$HERE/settings.conf" ]] || {
    echo 'ERROR: Bitte zuerst setup.sh ausführen.' >&2; exit 1;
}
source "$HERE/settings.conf"
export RADARR_CONTAINER SONARR_CONTAINER UPPOLLO_LOG_DIR
# Intentionally derived here: no worker from an old import directory is used.
export RADARR_SCRIPT_DIR='/scripts/arrporter/upload_cleanup/radarr'
export SONARR_SCRIPT_DIR='/scripts/arrporter/upload_cleanup/sonarr'
unset UPPOLLO_CONFIG
exec 9>"$HERE/.cleanup.lock"
flock -n 9 || { echo 'Cleanup läuft bereits.'; exit 0; }
if [[ "$MODE" == --diagnose ]]; then
    exec bash "$HERE/audit-cleanup.sh"
fi
failed=0
for app in radarr sonarr; do
    printf '\n=== %s Cleanup: %s ===\n' "$app" "$MODE"
    if bash "$HERE/$app/auto-cleanup-unraid.sh" "$MODE"; then
        printf '%s: Lauf erfolgreich beendet.\n' "$app"
    else
        code=$?
        printf 'ERROR: %s-Cleanup meldet Exit-Code %s; siehe Ausgabe.\n' "$app" "$code" >&2
        failed=$((failed + 1))
    fi
done
printf '\nFertig: %s Cleanup-Läufe mit Fehlern; Modus=%s\n' "$failed" "$MODE"
((failed == 0))

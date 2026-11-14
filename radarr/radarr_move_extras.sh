#!/usr/bin/env bash
set -Eeuo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
command -v python3 >/dev/null || { echo 'ERROR: Extras: python3 fehlt.' >&2; exit 1; }
case "${radarr_eventtype:-}" in
    Test) echo 'Extras: Test OK (Python vorhanden).'; exit 0;;
    Download) exec python3 "$HERE/radarr_extras.py";;
    *) exit 0;;
esac

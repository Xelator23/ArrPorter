#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
MODE=${1:---check}
[[ "$MODE" == --check || "$MODE" == --apply ]] || exit 2
BEFORE=${2:-$(date +%s)}
[[ "$BEFORE" =~ ^[0-9]+$ ]] || exit 2
set -a
source "$HERE/../../scripts/load-config.sh" "$HERE" cleanup
export SPECIAL_GROUP
set +a
exec python3 -B "$HERE/radarr_transit.py" duplicates "$MODE" "$BEFORE"

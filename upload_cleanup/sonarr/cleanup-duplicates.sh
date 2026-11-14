#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$HERE/cleanup.conf"
export SPECIAL_GROUP
export TRANSIT_BASE QBIT_URL QBIT_USER QBIT_PASS
exec python3 -B "$HERE/cleanup_duplicates.py" "$@"

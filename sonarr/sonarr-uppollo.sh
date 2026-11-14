#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "${UPPOLLO_CONFIG:-$HERE/uppollo.conf}"
source "$HERE/filters.conf"
for cmd in python3 jq; do command -v "$cmd" >/dev/null || { echo "ERROR: $cmd fehlt" >&2; exit 1; }; done
export SONARR_URL SONARR_API_KEY UPPOLLO_URL API_KEY QBIT_URL QBIT_USER QBIT_PASS TRANSIT_BASE DRY_RUN
export BLACKHOLE_BASE SPECIAL_GROUP
export CLIENT_UPLOAD_BASE CLIENT_SPECIAL_BASE
export FILTERS_JSON=$(
  {
    for tag in "${!ALLOWED_TAGS[@]}"; do printf 'tag\t%s\t%s\n' "$tag" "${ALLOWED_TAGS[$tag]}"; done
    for res in "${!ALLOWED_RESOLUTIONS[@]}"; do printf 'resolution\t%s\t%s\n' "$res" "${ALLOWED_RESOLUTIONS[$res]}"; done
    for group in "${EXCLUDED_GROUPS[@]}"; do printf 'group\t%s\n' "$group"; done
    for group in "${NFO_REQUIRED_GROUPS[@]}"; do printf 'nfo\t%s\n' "$group"; done
    for indexer in "${EXCLUDED_INDEXERS[@]}"; do printf 'indexer\t%s\n' "$indexer"; done
  } | jq -Rn '[inputs | split("\t")]'
)
exec python3 "$HERE/sonarr_uppollo.py" "$@"

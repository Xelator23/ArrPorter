#!/usr/bin/env bash
# Single Radarr hook: extras first, transit second. Keep siblings together.
set -Eeuo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
case "${radarr_eventtype:-}" in
    Test)
        bash "$HERE/radarr_move_extras.sh"
        exec bash "$HERE/radarr-uppollo.sh" --check
        ;;
    Download) ;;
    *) exit 0;;
esac
[[ -d ${radarr_movie_path:-} ]] || { echo 'ERROR: Filmordner fehlt.' >&2; exit 1; }
command -v flock >/dev/null
command -v sha256sum >/dev/null
movie=$(realpath -e -- "$radarr_movie_path")
key=$(printf %s "$movie" | sha256sum); key=${key%% *}
exec 7>"/tmp/radarr-import-chain-$key.lock"
# Do not discard an event if another import for this movie is still running.
flock -w 900 7 || { echo 'ERROR: Zeitlimit beim Warten auf den anderen Import.' >&2; exit 1; }
work=$(mktemp -d)
trap 'rm -rf -- "${work:?}"' EXIT
export RADARR_EXTRAS_MANIFEST="$work/manifest.json"
export RADARR_EXTRAS_PREVIEW="$work/extras"
mkdir -- "$RADARR_EXTRAS_PREVIEW"
bash "$HERE/radarr_move_extras.sh"
bash "$HERE/radarr-uppollo.sh"

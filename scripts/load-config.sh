#!/usr/bin/env bash
# Source with the application directory and optional "cleanup" mode.
# Run in the caller's shell so existing Bash configuration files keep their semantics.
set +x
arr_dir=$1
arr_app=${arr_dir##*/}
arr_mode=${2:-import}
case "$arr_app" in radarr|sonarr) ;; *) echo 'ERROR: Unbekannter Dienst.' >&2; return 1;; esac
if [[ "$arr_mode" == cleanup ]]; then
    arr_shared="$arr_dir/../../arrporter.conf"
    arr_legacy="$arr_dir/cleanup.conf"
else
    arr_shared="$arr_dir/../arrporter.conf"
    arr_legacy="${UPPOLLO_CONFIG:-$arr_dir/uppollo.conf}"
fi
if [[ "$arr_mode" == import && -n ${UPPOLLO_CONFIG:-} ]]; then
    source "$arr_legacy" || return 1
elif [[ -e "$arr_shared" || -L "$arr_shared" ]]; then
    source "$arr_shared" || return 1
    source "$(dirname -- "${BASH_SOURCE[0]}")/select-service.sh" "$arr_app" || return 1
else
    source "$arr_legacy" || return 1
fi
if [[ "$arr_mode" == cleanup ]]; then
    unset API_KEY RADARR_API_KEY SONARR_API_KEY
    CLIENT_UPLOAD_BASE=${CLIENT_UPLOAD_BASE:-/data/torrents/upload}
    CLIENT_SPECIAL_BASE=${CLIENT_SPECIAL_BASE:-/data/torrents/special}
    SPECIAL_GROUP=${SPECIAL_GROUP:-}
fi
unset arr_app arr_dir arr_mode arr_shared arr_legacy arr_radarr arr_sonarr

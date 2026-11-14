#!/usr/bin/env bash
set -Eeuo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
bash -n "$HERE/radarr-import-chain.sh"
bash -n "$HERE/radarr_move_extras.sh"
bash -n "$HERE/radarr-uppollo.sh"
ROOT=$(mktemp -d)
trap 'rm -rf -- "${ROOT:?}"' EXIT
mkdir -p "$ROOT/source/Subs/de" "$ROOT/source/Subs/en" "$ROOT/library"
printf 'nfo\n' > "$ROOT/source/original.nfo"
printf 'German\n' > "$ROOT/source/Subs/de/same.srt"
printf 'English\n' > "$ROOT/source/Subs/en/same.srt"
export RADARR_EXTRAS_MANIFEST="$ROOT/extras.json"
export radarr_eventtype=Download radarr_moviefile_sourcefolder="$ROOT/source" radarr_movie_path="$ROOT/library"
bash "$HERE/radarr_move_extras.sh"
cmp "$ROOT/source/original.nfo" "$ROOT/library/original.nfo"
cmp "$ROOT/source/Subs/de/same.srt" "$ROOT/library/Subs/de/same.srt"
cmp "$ROOT/source/Subs/en/same.srt" "$ROOT/library/Subs/en/same.srt"
bash "$HERE/radarr_move_extras.sh"
printf 'Different\n' > "$ROOT/source/Subs/de/same.srt"
if bash "$HERE/radarr_move_extras.sh"; then echo 'FAIL: Konflikt akzeptiert'; exit 1; fi
[[ $(cat "$ROOT/library/original.nfo") == nfo ]]
radarr_eventtype=Test bash "$HERE/radarr_move_extras.sh"
echo 'PASS: Originalnamen, Unterordner, identische Wiederholung und Konfliktschutz.'

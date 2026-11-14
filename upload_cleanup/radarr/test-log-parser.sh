#!/usr/bin/env bash
set -Eeuo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
parse() { awk -f "$HERE/uppollo-completions.awk"; }
start='time=2026-09-05T10:25:04.667+02:00 level=INFO msg="New Upload: /data/.uppollo_transit/Example"'
finish='time=2026-09-05T10:25:31.197+02:00 level=INFO msg="Upload processed successfully"'
fail='time=2026-09-05T10:25:31.197+02:00 level=ERROR msg="Error during upload processing: all tracker uploads failed"'
expected=$'2026-09-05T10:25:04.667+02:00\t2026-09-05T10:25:31.197+02:00\t/data/.uppollo_transit/Example'
[[ $(printf '%s\n' "$start" "$finish" | parse) == "$expected" ]]
[[ -z $(printf '%s\n' "$start" "$fail" | parse) ]]
[[ -z $(printf '%s\n' "$finish" | parse) ]]
[[ -z $(printf '%s\n' "$start" "$start" "$finish" "$finish" | parse) ]]
[[ -z $(printf '%s\n' "$start" | parse) ]]
[[ $(printf '%s\n' "$start" "$fail" "$start" "$finish" | parse) == "$expected" ]]
[[ $(printf '%s\n' "$start" "$finish" "$start" "$finish" | parse | wc -l) == 2 ]]
echo 'PASS: Erfolg, Fehler, fehlender Start, Überlappung, fehlender Abschluss und Folgeaufträge.'

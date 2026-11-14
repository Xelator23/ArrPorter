#!/usr/bin/env bash
set -Eeuo pipefail
root=$(cd -- "$(dirname -- "$0")/.." && pwd)
fixture=$(mktemp -d)
trap 'rm -rf -- "${fixture:?}"' EXIT
mkdir -p "$fixture"/{radarr,sonarr,scripts,upload_cleanup/{radarr,sonarr}}
cp "$root/scripts/"{migrate-config,load-config,select-service}.sh "$fixture/scripts/"
cd "$fixture"
for app in radarr sonarr; do
    cat > "$app/uppollo.conf" <<'COMMON'
QBIT_URL='http://qbit.example.invalid'
QBIT_USER='test'
QBIT_PASS='literal $(false) $var with spaces'
API_KEY='test-only'
UPPOLLO_URL='http://test.invalid/api/upload'
COMMON
    printf 'TRANSIT_BASE=/transit/%s\n' "$app" >> "$app/uppollo.conf"
    printf 'TRANSIT_BASE=/old/%s\n' "$app" > "upload_cleanup/$app/cleanup.conf"
done
echo 'DRY_RUN=false' >> radarr/uppollo.conf
echo "RADARR_API_KEY='r-test'" >> radarr/uppollo.conf
echo "SONARR_API_KEY='s-test'" >> sonarr/uppollo.conf
loader="$PWD/scripts/load-config.sh"
(source "$loader" "$PWD/radarr"; [[ "$TRANSIT_BASE" == /transit/radarr ]])
(source "$loader" "$PWD/upload_cleanup/sonarr" cleanup; [[ "$TRANSIT_BASE" == /old/sonarr ]])
echo 'QBIT_PASS=other-test-only' >> sonarr/uppollo.conf
if bash scripts/migrate-config.sh >log 2>&1; then exit 1; fi
grep -q 'QBIT_PASS' log
if grep -Eq 'other-test-only|literal' log || [[ -e arrporter.conf ]]; then exit 1; fi
sed -i '$d' sonarr/uppollo.conf
bash scripts/migrate-config.sh
if bash scripts/migrate-config.sh; then exit 1; fi
(source "$loader" "$PWD/sonarr"; [[ "$DRY_RUN" == true && "$TRANSIT_BASE" == /transit/sonarr && "$SONARR_API_KEY" == s-test && ! -v RADARR_API_KEY ]])
(source "$loader" "$PWD/radarr"; [[ "$DRY_RUN" == false && "$RADARR_API_KEY" == r-test && ! -v SONARR_API_KEY ]])
(source "$loader" "$PWD/upload_cleanup/radarr" cleanup; [[ "$QBIT_PASS" == 'literal $(false) $var with spaces' && "$TRANSIT_BASE" == /transit/radarr && ! -v API_KEY && ! -v RADARR_API_KEY ]])
echo 'QBIT_PASS=rotated-test' >> arrporter.conf
(source "$loader" "$PWD/upload_cleanup/sonarr" cleanup; [[ "$QBIT_PASS" == rotated-test && "$TRANSIT_BASE" == /transit/sonarr ]])
(UPPOLLO_CONFIG="$PWD/radarr/uppollo.conf"; source "$loader" "$PWD/radarr"; [[ "$QBIT_PASS" != rotated-test ]])
echo 'SONARR_TRANSIT_BASE=/transit/radarr/nested' >> arrporter.conf
if (source "$loader" "$PWD/sonarr"); then exit 1; fi
echo 'PASS: Migration, Konflikte, Alt-Konfiguration, Dienstzuordnung und gemeinsame Cleanup-Zugangsdaten.'

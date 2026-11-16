#!/usr/bin/env bash
# Offline integration tests. Requires the same Linux dependencies as the hook.
# All HTTP requests are intercepted by a fake curl; no live services are contacted.
set -Eeuo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
for cmd in python3 bash jq flock realpath sha256sum stat find sort ln mktemp; do
  command -v "$cmd" >/dev/null || { echo "Missing: $cmd"; exit 1; }
done
bash -n "$HERE/radarr-uppollo.sh"
ROOT=$(mktemp -d)
trap 'rm -rf -- "${ROOT:?}"' EXIT
mkdir -p "$ROOT/bin" "$ROOT/media" "$ROOT/upload"
# The HTTP fixture for the Python srrDB reader is local to these test processes.
cat > "$ROOT/bin/sitecustomize.py" <<'PY'
import io
import json
import os
import urllib.request

class Fixture:
    def open(self, url, timeout):
        assert url.startswith('https://api.srrdb.com/v1/details/'), url
        if os.getenv('MOCK_SRRDB_MISSING'):
            release = url.rsplit('/', 1)[1]
            result = {'name': release, 'files': [], 'archived-files': [
                {'name': 'original.mkv', 'size': 14},
                {'name': 'Subs/forced.idx', 'size': 12}]}
        else:
            result = []
        return io.BytesIO(json.dumps(result).encode())

urllib.request.build_opener = lambda *handlers: Fixture()
PY
export PYTHONPATH="$ROOT/bin"
cat > "$ROOT/filters.conf" <<'FILTERS'
EXCLUDED_INDEXERS=('blocked-indexer')
EXCLUDED_GROUPS=()
NFO_REQUIRED_GROUPS=()
declare -A ALLOWED_TAGS=([TESTBLOCK]=false)
declare -A ALLOWED_RESOLUTIONS=([1080p]=true [2160p]=false)
FILTERS
export RADARR_EXTRAS_MANIFEST="$ROOT/extras.json"
jq -n --arg dest "$ROOT/media" '{destination:$dest,files:[]}' > "$RADARR_EXTRAS_MANIFEST"
printf 'video fixture\n' > "$ROOT/media/video.mkv"
cat > "$ROOT/bin/curl" <<'MOCK'
#!/usr/bin/env bash
set -eu
out='' cookie='' payload='' url=''
while (($#)); do
 case "$1" in
 --output) out=$2; shift 2;;
 --cookie-jar) cookie=$2; shift 2;;
 --data-binary) payload=${2#@}; shift 2;;
 *) url=$1; shift;;
 esac
done
case "$url" in
 */auth/login) printf '%s' "${MOCK_LOGIN:-Ok.}" > "$out"; touch "$cookie";;
 */torrents/trackers) printf '[{"url":"https://%s/announce"}]' "${MOCK_TRACKER:-allowed.invalid}" > "$out";;
 */api/upload)
   cp "$payload" "$MOCK_CAPTURE"
   printf 'called\n' >> "$MOCK_CALLS"
   printf '{}' > "$out"
   [[ ${MOCK_TIMEOUT:-false} != true ]] || exit 28
   printf '%s' "${MOCK_STATUS:-200}";;
 *) echo "Unexpected HTTP request: $url" >&2; exit 99;;
esac
MOCK
chmod +x "$ROOT/bin/curl"
export PATH="$ROOT/bin:$PATH"
export radarr_eventtype=Download radarr_download_id=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
export radarr_download_client_type=QBittorrent
export radarr_movie_path="$ROOT/media" radarr_moviefile_path="$ROOT/media/video.mkv"
export radarr_moviefile_sourcepath='/downloads/Original Folder (2026)/Original.Download.Name.mkv'
export radarr_moviefile_sourcefolder='/downloads/Original Folder (2026)/'
FOLDER='Original Folder (2026)'
export radarr_moviefile_quality='Bluray-1080p' radarr_moviefile_scenename='Example.2026.1080p.BluRay-ExampleGroup'
export radarr_movie_imdbid=tt1234567 radarr_movie_tmdbid=123
export MOCK_CAPTURE="$ROOT/payload.json" MOCK_CALLS="$ROOT/calls"
export UPPOLLO_CONFIG="$ROOT/test.conf"
setup_case() {
  CASE=$1
  unset MOCK_STATUS MOCK_TIMEOUT MOCK_TRACKER MOCK_LOGIN
  cat > "$UPPOLLO_CONFIG" <<EOF
UPPOLLO_URL='http://mock.invalid/api/upload'
API_KEY='test-only'
QBIT_URL='http://mock.invalid'
QBIT_USER='test'
QBIT_PASS='test'
TRANSIT_BASE='$ROOT/transit-$CASE'
FILTER_CONFIG='$ROOT/filters.conf'
CLIENT_UPLOAD_BASE='$ROOT/upload'
SPECIAL_GROUP='ExampleGroup'
CLIENT_SPECIAL_BASE='$ROOT/upload'
DRY_RUN=false
EOF
}
run() { bash "$HERE/radarr-uppollo.sh" "$@"; }
assert_state() {
  local files=("$ROOT/transit-$CASE/_state/"*.json)
  jq -e --arg expected "$1" '.status == $expected' "${files[0]}" >/dev/null
}
setup_case dry
run --dry-run
[[ ! -e "$MOCK_CALLS" && ! -d "$ROOT/transit-dry/$FOLDER" ]]
echo 'PASS: dry run without POST or transit release folder'
setup_case srrdb-missing
if MOCK_SRRDB_MISSING=true run; then exit 1; fi
grep -Fq 'fehlt: Subs/forced.idx' "$ROOT/transit-srrdb-missing/_logs/"*.log
[[ ! -e "$MOCK_CALLS" && ! -d "$ROOT/transit-srrdb-missing/$FOLDER" ]]
echo 'PASS: srrDB names missing subtitle and stops before staging or POST'
setup_case accepted
run
assert_state accepted_unverified
jq -e '.Category == "ExampleGroup" and .TMDBID == 123' "$MOCK_CAPTURE" >/dev/null
[[ "$radarr_moviefile_path" -ef "$ROOT/transit-accepted/$FOLDER/Original.Download.Name.mkv" ]]
[[ ! -e "$ROOT/transit-accepted/$FOLDER/video.mkv" ]]
run
[[ $(wc -l < "$MOCK_CALLS") == 1 ]]
echo 'PASS: accepted, hardlink, category, duplicate prevention, retained transit'
setup_case timeout
export MOCK_TIMEOUT=true
if run; then echo 'FAIL: timeout accepted'; exit 1; fi
assert_state outcome_unknown
echo 'PASS: timeout remains ambiguous and keeps files'
setup_case http_error
export MOCK_STATUS=500
if run; then echo 'FAIL: HTTP error accepted'; exit 1; fi
assert_state http_error
echo 'PASS: HTTP error'
setup_case blocked
export MOCK_TRACKER=blocked-indexer.invalid
run
[[ ! -d "$ROOT/transit-blocked/$FOLDER" ]]
echo 'PASS: blocked tracker'
setup_case login
export MOCK_LOGIN=Fails.
if run; then echo 'FAIL: bad login accepted'; exit 1; fi
echo 'PASS: bad login'
setup_case unsafe
export radarr_moviefile_scenename='../Example.1080p-ExampleGroup'
if run; then echo 'FAIL: unsafe name accepted'; exit 1; fi
echo 'PASS: unsafe release name'
setup_case resolution
export radarr_moviefile_scenename='Example.2160p-ExampleGroup'
run
[[ ! -d "$ROOT/transit-resolution/$FOLDER" ]]
echo 'PASS: forbidden resolution'
setup_case usenet
export radarr_download_client_type=Sabnzbd radarr_download_id=SABnzbd_nzo_example
export radarr_release_indexer='ExampleIndexer (Prowlarr)'
export radarr_moviefile_scenename='Example.2026.1080p.BluRay-ExampleGroup'
# Any accidental qBittorrent login must fail this case.
export MOCK_LOGIN=Fails.
run --dry-run
grep -q 'DRY RUN OK' "$ROOT/transit-usenet/_logs/"*.log
echo 'PASS: SABnzbd ID accepted without qBittorrent login'
setup_case usenet_blocked
export radarr_release_indexer='blocked-indexer'
run
[[ ! -d "$ROOT/transit-usenet_blocked/$FOLDER" ]]
grep -q 'Gesperrter Usenet-Indexer' "$ROOT/transit-usenet_blocked/_logs/"*.log
echo 'PASS: Usenet indexer blacklist'
setup_case unknown_client
export radarr_download_client_type=Unknown
if run; then echo 'FAIL: unknown client accepted'; exit 1; fi
echo 'PASS: unknown client is not mistaken for Usenet'
setup_case direct_group
export radarr_download_client_type=Sabnzbd radarr_release_indexer=ExampleIndexer
export radarr_moviefile_scenename='Example.2026.1080p-sNk'
run
assert_state accepted_unverified
[[ "$radarr_moviefile_path" -ef "$ROOT/transit-direct_group/Original.Download.Name.mkv" ]]
jq -e --arg path "$ROOT/transit-direct_group/Original.Download.Name.mkv" '.Path == $path' "$MOCK_CAPTURE" >/dev/null
echo 'PASS: erlaubte Gruppe ohne Beachtung der Schreibweise direkt übergeben'
setup_case target_exists
mkdir "$ROOT/upload/$FOLDER"
export radarr_moviefile_scenename='Example.2026.1080p-OTHER'
if run; then echo 'FAIL: vorhandenes Ziel akzeptiert'; exit 1; fi
[[ ! -e "$ROOT/transit-target_exists/$FOLDER" ]]
rmdir "$ROOT/upload/$FOLDER"
echo 'PASS: vorhandenes Upload-Ziel stoppt vor der Vorbereitung'
setup_case missing_original
export radarr_download_client_type=Sabnzbd radarr_release_indexer=ExampleIndexer
unset radarr_moviefile_sourcepath
if run; then echo 'FAIL: missing original name accepted'; exit 1; fi
echo 'PASS: missing original filename stops the upload'
setup_case missing_folder
export radarr_moviefile_sourcepath='/downloads/Original.Download.Name.mkv'
unset radarr_moviefile_sourcefolder
if run; then echo 'FAIL: missing source folder accepted'; exit 1; fi
echo 'PASS: missing original folder stops the upload'
mkdir -p "$ROOT/download/$FOLDER"
export radarr_moviefile_sourcefolder="$ROOT/download/$FOLDER"
export radarr_moviefile_sourcepath="$radarr_moviefile_sourcefolder/Original.Download.Name.mkv"
printf 'library sidecar\n' > "$ROOT/media/video.nfo"
printf 'old\n' > "$ROOT/media/release.nfo"
setup_case chain_srrdb_failure
if MOCK_SRRDB_MISSING=true bash "$HERE/radarr-import-chain.sh"; then exit 1; fi
[[ $(cat "$ROOT/media/video.nfo") == 'library sidecar' && -f "$ROOT/media/release.nfo" ]]
printf 'new\n' > "$radarr_moviefile_sourcefolder/release.nfo"
setup_case chain_dry
sed -i 's/^DRY_RUN=false$/DRY_RUN=true/' "$UPPOLLO_CONFIG"
bash "$HERE/radarr-import-chain.sh"
grep -q 'DRY RUN OK' "$ROOT/transit-chain_dry/_logs/"*.log
[[ $(cat "$ROOT/media/release.nfo") == old && -f "$ROOT/media/video.nfo" ]]
setup_case chain_live
bash "$HERE/radarr-import-chain.sh"
cmp "$radarr_moviefile_sourcefolder/release.nfo" "$ROOT/media/release.nfo"
cmp "$ROOT/media/release.nfo" "$ROOT/transit-chain_live/$FOLDER/release.nfo"
[[ ! -e "$ROOT/media/video.nfo" ]]
setup_case chain_no_nfo
rm -- "$radarr_moviefile_sourcefolder/release.nfo"
printf 'library sidecar\n' > "$ROOT/media/video.nfo"
bash "$HERE/radarr-import-chain.sh"
[[ -f "$ROOT/media/video.nfo" && ! -e "$ROOT/transit-chain_no_nfo/$FOLDER/video.nfo" ]]
echo 'PASS: library NFO protected on srrDB failure, dry run and ambiguous empty source; live upgrade uses original'
echo 'All offline tests passed.'

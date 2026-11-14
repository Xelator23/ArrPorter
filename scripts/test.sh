#!/usr/bin/env bash
# Offline verification on Linux. No live service configuration is read.
set -Eeuo pipefail
cd -- "$(dirname -- "$0")/.."
find radarr sonarr upload_cleanup scripts -name '*.sh' -exec bash -n {} \;
bash scripts/test-config.sh
python3 -B -m unittest discover -s scripts -p 'test_srrdb.py' -q
for dir in radarr sonarr upload_cleanup/radarr upload_cleanup/sonarr upload_cleanup; do
    (cd "$dir" && python3 -B -m unittest discover -q)
done
bash radarr/test-extras.sh
bash radarr/test-offline.sh
bash upload_cleanup/radarr/test-log-parser.sh

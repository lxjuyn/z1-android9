#!/usr/bin/env bash
set -euo pipefail
REPO_URL=${Z1_ANDROID9_REPO_URL:-https://github.com/lxjuyn/z1-android9.git}
WORKSPACE=${1:?usage: sync_sources.sh /path/to/workspace}
mkdir -p "$WORKSPACE"
cd "$WORKSPACE"
repo init -u "$REPO_URL" -b main -m default.xml
repo sync -j"${SYNC_JOBS:-4}"
python3 device/jiabo/z1/scripts/apply_stock_patches.py --source "$PWD"

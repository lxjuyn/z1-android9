#!/bin/sh
set -eu
storage_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
repo_root=$(CDPATH= cd -- "$storage_dir/../../../../.." && pwd)
core="$repo_root/android9/los16/system/core"
test_bin=$(mktemp /tmp/z1-fuse-permissions.XXXXXX)
trap 'rm -f "$test_bin"' EXIT HUP INT TERM
g++ -std=c++14 -ffunction-sections -fdata-sections -Wno-unused-parameter \
    -I"$core/base/include" -I"$core/libcutils/include" \
    -I"$core/libpackagelistparser/include" \
    "$storage_dir/tests/permissions_host.cpp" "$core/libcutils/multiuser.cpp" \
    -Wl,--gc-sections -o "$test_bin"
"$test_bin"

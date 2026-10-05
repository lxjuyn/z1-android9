#!/bin/sh
# Build the repository's Apache-2.0 MTK mkbootimg variant for the host.
set -eu

source_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH= cd -- "$source_dir/../.." && pwd)
output=${1:-"$repo_root/out/host/bin/mtkbootimg"}
compiler=${CC:-cc}

if ! command -v "$compiler" >/dev/null 2>&1; then
    echo "ERROR: host C compiler not found: $compiler" >&2
    exit 1
fi
if ! command -v pkg-config >/dev/null 2>&1 || ! pkg-config --exists openssl; then
    echo "ERROR: host OpenSSL development files and pkg-config are required" >&2
    exit 1
fi

mkdir -p "$(dirname -- "$output")"
temporary_output="$output.tmp.$$"
trap 'rm -f "$temporary_output"' EXIT HUP INT TERM

# This source uses OpenSSL's host SHA-1 API in place of Android mincrypt and
# preserves the legacy MTK v0 header and payload-writing behavior.
# shellcheck disable=SC2046
"$compiler" -std=c99 -O2 -Wall -Wextra -Wno-deprecated-declarations \
    -I "$source_dir" "$source_dir/mkbootimg.c" -o "$temporary_output" \
    $(pkg-config --cflags --libs openssl)
chmod 0755 "$temporary_output"
mv -f "$temporary_output" "$output"

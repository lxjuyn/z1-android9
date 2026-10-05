#!/usr/bin/env bash
# Compile only the freestanding-from-/system stock ramdisk mount guard.
set -euo pipefail
STOCK_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
if [[ $# != 1 ]]; then
    echo "Usage: bash stock/build_mount_guard.sh OUTPUT" >&2
    exit 2
fi
GUARD_CC=${Z1_GUARD_CC:-arm-linux-gnueabihf-gcc}
GUARD_OUTPUT=$1
mkdir -p -- "$(dirname "$GUARD_OUTPUT")"
GUARD_TEMP="${GUARD_OUTPUT}.tmp.$$"
trap 'rm -f -- "$GUARD_TEMP"' EXIT
"$GUARD_CC" -static -Os -Wall -Wextra -Werror \
    -march=armv7-a -mfpu=vfpv3-d16 -mfloat-abi=hard \
    "$STOCK_DIR/z1_stock_mount_guard.c" -o "$GUARD_TEMP"
chmod 0755 -- "$GUARD_TEMP"
mv -f -- "$GUARD_TEMP" "$GUARD_OUTPUT"

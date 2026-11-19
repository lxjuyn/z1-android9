#!/bin/bash
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$HERE/../../../../.." && pwd)
NDK=${NDK:-"$ROOT/android-ndk-r26d"}
CC=${CC:-"$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/armv7a-linux-androideabi28-clang"}
OUT=${1:?usage: build.sh <outdir>}
SHIM="$ROOT/z1a9_gpt/scripts/hal_stub"

[ -x "$CC" ] || {
    echo "ERROR: Android ARM compiler not found: $CC" >&2
    exit 1
}

mkdir -p "$OUT"
"$CC" -shared -fPIC -O2 -Wall -Wextra -Werror \
    -fvisibility=hidden \
    -I"$SHIM" \
    -Wl,-soname,gps.default.so \
    -Wl,--no-undefined \
    -o "$OUT/gps.default.so" \
    "$HERE/gps_adapter.c" \
    -ldl -llog

for symbol in HMI gps__get_gps_interface; do
    case "$symbol" in
        HMI)
            arm-linux-gnueabihf-readelf -Ws "$OUT/gps.default.so" |
                grep -Eq 'GLOBAL +DEFAULT +[0-9]+ HMI$' || {
                    echo "ERROR: missing HMI export" >&2
                    exit 1
                }
            ;;
        gps__get_gps_interface)
            strings -a "$OUT/gps.default.so" | grep -Fq "$symbol" || {
                echo "ERROR: missing factory interface lookup" >&2
                exit 1
            }
            ;;
    esac
done

arm-linux-gnueabihf-readelf -d "$OUT/gps.default.so" |
    grep -Fq 'Library soname: [gps.default.so]' || {
        echo "ERROR: wrong SONAME" >&2
        exit 1
    }

printf 'built %s (%s bytes)\n' "$OUT/gps.default.so" \
    "$(stat -c %s "$OUT/gps.default.so")"

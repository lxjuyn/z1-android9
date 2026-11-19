#!/bin/bash
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$HERE/../../../../../.." && pwd)
IMAGE=${1:-"$ROOT/out/z1_gps_offline_candidate_20261119/system.img"}
NDK=${NDK:-"$ROOT/android-ndk-r26d"}
CC="$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/armv7a-linux-androideabi28-clang"
SHIM="$ROOT/z1a9_gpt/scripts/hal_stub"
QEMU=${QEMU:-qemu-arm-static}

[ -f "$IMAGE" ] || {
    echo "ERROR: Android system image not found: $IMAGE" >&2
    exit 1
}
[ -x "$CC" ] || {
    echo "ERROR: Android ARM compiler not found: $CC" >&2
    exit 1
}
[ -x "$(command -v "$QEMU" 2>/dev/null)" ] || {
    echo "ERROR: qemu not found: $QEMU" >&2
    exit 1
}

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT HUP INT TERM
guest="$work/root"
mkdir -p "$guest/system/bin" "$guest/system/lib" "$guest/vendor/lib/hw"

extract() {
    source_path=$1
    target_path=$2
    debugfs -R "dump -p $source_path $target_path" "$IMAGE" \
        >/dev/null 2>&1 || {
            echo "ERROR: cannot extract $source_path" >&2
            exit 1
        }
}

extract /bin/linker "$guest/system/bin/linker"
for library in libc.so libc++.so libdl.so liblog.so libm.so libstdc++.so \
    libcutils.so libhardware.so libvndksupport.so libxlog.so; do
    extract "/lib/$library" "$guest/system/lib/$library"
done
extract /vendor/lib/hw/gps.default.so "$guest/vendor/lib/hw/gps.default.so"
extract /vendor/lib/hw/gps.z1stock.so "$guest/vendor/lib/hw/gps.z1stock.so"

"$CC" -fPIE -pie -O2 -Wall -Wextra -Werror -I"$SHIM" \
    -o "$guest/probe" "$HERE/probe_actual_hal.c" -ldl

"$QEMU" -L "$guest" "$guest/probe"

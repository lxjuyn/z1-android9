#!/bin/bash
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$HERE/../../../../../.." && pwd)
IMAGE=${1:-"$ROOT/out/adaptation_candidate_20261115_flash/system.img"}
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
mkdir -p "$guest/system/bin" "$guest/system/lib" \
    "$guest/vendor/lib/hw"

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
extract /lib/libc.so "$guest/system/lib/libc.so"
extract /lib/libc++.so "$guest/system/lib/libc++.so"
extract /lib/libdl.so "$guest/system/lib/libdl.so"
extract /lib/liblog.so "$guest/system/lib/liblog.so"
extract /lib/libm.so "$guest/system/lib/libm.so"

compiler_common=(
    -shared -fPIC -O2 -Wall -Wextra -Werror
    -I"$SHIM"
    -Wl,--no-undefined
)
"$CC" "${compiler_common[@]}" -fvisibility=hidden \
    -DZ1_STOCK_GPS_HAL_PATH='"/vendor/lib/hw/gps.z1stock.so"' \
    -Wl,-soname,gps.default.so \
    -o "$guest/vendor/lib/hw/gps.default.so" \
    "$HERE/../gps_adapter.c" -ldl -llog
"$CC" "${compiler_common[@]}" \
    -Wl,-soname,gps.z1stock.so \
    -o "$guest/vendor/lib/hw/gps.z1stock.so" \
    "$HERE/mock_stock.c"
"$CC" -fPIE -pie -O2 -Wall -Wextra -Werror -I"$SHIM" \
    -o "$guest/test" "$HERE/test_adapter.c" -ldl -llog

if unshare -Urpf --mount-proc true >/dev/null 2>&1; then
    unshare -Urpf --mount-proc "$QEMU" -L "$guest" "$guest/test"
else
    "$QEMU" -L "$guest" "$guest/test"
fi

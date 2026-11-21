#!/bin/bash
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$HERE/../../../.." && pwd)
IMAGE=${1:-"$ROOT/out/r6_audio_candidate_20261119/system.img"}
NDK=${NDK:-"$ROOT/android-ndk-r26d"}
CC="$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/armv7a-linux-androideabi28-clang"
QEMU=${QEMU:-qemu-arm-static}

[ -f "$IMAGE" ] || {
    echo "ERROR: Android system image not found: $IMAGE" >&2
    exit 1
}
[ -x "$CC" ] || {
    echo "ERROR: Android ARM compiler not found: $CC" >&2
    exit 1
}
[ -x "$(command -v "$QEMU")" ] || {
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
for file in libc.so libc++.so libm.so libdl.so liblog.so libtinyalsa.so; do
    extract "/lib/$file" "$guest/system/lib/$file"
done
for target in /vendor/lib/hw/audio.primary.mt6572.so \
              /vendor/lib/libaudio.primary.default.so \
              /vendor/lib/hw/audio.primary.default.so; do
    extract "$target" "$guest$target"
done

"$CC" -fPIE -pie -O2 -Wall -Wextra -Werror \
    -I"$HERE/../inc" \
    -Wl,--no-undefined -o "$guest/test" \
    "$HERE/test_deployed_hal.c" -ldl

if [ "$$" -gt 65535 ]; then
    echo "SKIP: 32-bit bionic rejects host pid $$ (>65535); run in a PID namespace" >&2
    exit 77
fi

"$QEMU" -L "$guest" "$guest/test" /vendor/lib/hw/audio.primary.mt6572.so
"$QEMU" -L "$guest" "$guest/test" /vendor/lib/hw/audio.primary.default.so
"$QEMU" -L "$guest" "$guest/test" /vendor/lib/libaudio.primary.default.so

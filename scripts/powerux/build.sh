#!/bin/bash
# Build hwcomposer.z1.so from the Z1 source tree with the NDK.
# PROVEN 2026-11-08: stock source and the backlight-patched source both compile
# and link with NDK r26d + AOSP r61 headers (see /tmp/powerux/hwc/include).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
NDK=${NDK:-/home/lxj/claude/6572/android-ndk-r26d/toolchains/llvm/prebuilt/linux-x86_64}
SRC=${1:-"$HERE/hwcomposer_patched.cpp"}
OUT=${2:-"$HERE/hwcomposer.z1.so"}
CC=$NDK/bin/armv7a-linux-androideabi28-clang++
INC=$HERE/include
# libhardware.so is not in the NDK; link against the copy dumped from the device
# image (only used to resolve hw_get_module at link time).
LIBS=$HERE/libs

$CC -c -fPIC -O2 -I"$INC" -DLOG_TAG='"hwcomposer"' -o "$HERE/hwcomposer.o" "$SRC"
$CC -shared -fPIC -Wl,-soname,hwcomposer.z1.so -Wl,--allow-shlib-undefined \
    -o "$OUT" "$HERE/hwcomposer.o" -L"$LIBS" -lhardware \
    -L"$NDK/sysroot/usr/lib/arm-linux-androideabi/28" -llog -lsync -static-libstdc++
echo "built $OUT"
readelf -d "$OUT" | grep -E 'NEEDED|SONAME'

#!/bin/bash
# 编 z1drmtest（arm32，静态链 libdrm，可直接丢进 ramdisk 跑）
set -eu
NDK=/home/lxj/claude/6572/android-ndk-r26d
CC=$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/armv7a-linux-androideabi28-clang
HERE=$(cd "$(dirname "$0")" && pwd)
INC=/home/lxj/claude/6572/mesa_android/libdrm-install/include
LIB=/home/lxj/claude/6572/mesa_android/libdrm-static-build/libdrm.a
OUT=${1:-/tmp/z1drmtest}
mkdir -p "$OUT"
$CC -O2 -Wall -o "$OUT/z1drmtest" "$HERE/z1drmtest.c" -I"$INC" -I"$INC/libdrm" "$LIB"
echo "built $OUT/z1drmtest ($(stat -c%s "$OUT/z1drmtest") B)"
file "$OUT/z1drmtest"

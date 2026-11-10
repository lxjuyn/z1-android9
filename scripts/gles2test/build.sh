#!/usr/bin/env bash
# Offline only: no adb, install, target execution or driver changes.
set -euo pipefail
umask 077
export PYTHONDONTWRITEBYTECODE=1
HERE=$(dirname "$(readlink -f "$0")")
ROOT=/home/lxj/claude/6572
NDK=${ANDROID_NDK_ROOT:-$ROOT/android-ndk-r26d}
BIN="$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin"
SYSROOT="$NDK/toolchains/llvm/prebuilt/linux-x86_64/sysroot"
grep -q '^Pkg.Revision = 26.3.11579264$' "$NDK/source.properties"
test -x "$BIN/armv7a-linux-androideabi28-clang"
test -d "$ROOT/bridge_backups/products"
STAMP=$(date +%Y%m%d_%H%M%S_%N)
ARCHIVE="$ROOT/bridge_backups/products/products_gles2test_$STAMP"
BUILD="$ARCHIVE/build"
mkdir "$ARCHIVE" "$BUILD"
# Keep sources and all outputs outside the git worktree.
cp -p "$HERE/gles2probe.c" "$HERE/analyze.py" "$HERE/test_analyze.py" "$HERE/build.sh" "$HERE/check_build.py" "$ARCHIVE/"
(
COMMON=(-std=c11 -O2 -g -Wall -Wextra -Werror -Wshadow -Wformat=2 -fno-common)
"$BIN/armv7a-linux-androideabi28-clang" --version
"$BIN/armv7a-linux-androideabi28-clang" "${COMMON[@]}" -march=armv7-a -mfpu=vfpv3-d16 -mfloat-abi=softfp -fPIE -pie -Wl,-z,relro,-z,now -Wl,--build-id=sha1 "$HERE/gles2probe.c" -lEGL -lGLESv2 -o "$BUILD/gles2probe_android28_armv7"
cp -p "$BUILD/gles2probe_android28_armv7" "$ARCHIVE/"
sha256sum "$BUILD/gles2probe_android28_armv7" "$ARCHIVE/gles2probe_android28_armv7" | tee "$BUILD/target_sha256.txt"
"$BIN/llvm-readelf" -h -l -d -A -n "$BUILD/gles2probe_android28_armv7" > "$BUILD/target_elf.txt"
"$BIN/llvm-readelf" --dyn-syms "$BUILD/gles2probe_android28_armv7" > "$BUILD/target_symbols.txt"
python3 "$HERE/check_build.py" "$BUILD"
# Isolate Khronos headers from the NDK libc headers for native host testing.
mkdir "$BUILD/host-include"
cp -a "$SYSROOT/usr/include/EGL" "$SYSROOT/usr/include/GLES2" "$SYSROOT/usr/include/KHR" "$BUILD/host-include/"
cc "${COMMON[@]}" -DUSE_OZONE -I"$BUILD/host-include" "$HERE/gles2probe.c" -Wl,-l:libEGL.so.1 -Wl,-l:libGLESv2.so.2 -o "$BUILD/gles2probe_host"
cp -p "$BUILD/gles2probe_host" "$ARCHIVE/"
PYTHONDONTWRITEBYTECODE=1 python3 "$HERE/test_analyze.py" 2>&1 | tee "$BUILD/unit_tests.log"
env LIBGL_ALWAYS_SOFTWARE=1 GALLIUM_DRIVER=llvmpipe EGL_PLATFORM=surfaceless MESA_SHADER_CACHE_DISABLE=true timeout --signal=TERM --kill-after=5s 120s "$BUILD/gles2probe_host" --out "$BUILD/host_capture" --frames 3 > "$BUILD/host_run_stdout.log" 2>&1
PYTHONDONTWRITEBYTECODE=1 python3 "$HERE/analyze.py" "$BUILD/host_capture" --expected-frames 3 > "$BUILD/host_analysis.json"
python3 "$HERE/check_build.py" "$BUILD" --host
cp -p "$BUILD/status.json" "$BUILD/target_elf.txt" "$BUILD/target_symbols.txt" "$BUILD/target_sha256.txt" "$BUILD/unit_tests.log" "$BUILD/host_analysis.json" "$HERE/check_build.py" "$ARCHIVE/"
cp -a "$BUILD/host_capture" "$ARCHIVE/"
) 2>&1 | tee "$BUILD/build.log"
# Close the build log before hashing the complete archive.
python3 "$HERE/check_build.py" "$BUILD" --archive "$ARCHIVE"

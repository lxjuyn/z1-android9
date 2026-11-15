#!/bin/sh
set -eu
BASE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$BASE/../../.." && pwd)
NDK="$ROOT/android-ndk-r26d/toolchains/llvm/prebuilt/linux-x86_64/bin/armv7a-linux-androideabi28-clang"
STRIP="$ROOT/android-ndk-r26d/toolchains/llvm/prebuilt/linux-x86_64/bin/llvm-strip"
OUT="$BASE/build"
STAMP=$(date +%Y%m%d_%H%M%S)
ARCHIVE="$ROOT/bridge_backups/products/products_audio_regread_$STAMP"
mkdir -p "$OUT" "$ARCHIVE"
CFLAGS="-std=c11 -O2 -g0 -Wall -Wextra -Werror -Wconversion -Wshadow -Wformat=2 -fPIE -D_FORTIFY_SOURCE=2"
cc=${CC:-cc}
$cc $CFLAGS -fsyntax-only "$BASE/regread.c"
$cc $CFLAGS -o "$OUT/regread-host" "$BASE/regread.c"
strip --strip-all "$OUT/regread-host" 2>/dev/null || true
[ -x "$NDK" ] || { echo "missing NDK compiler: $NDK" >&2; exit 1; }
"$NDK" $CFLAGS -fPIE -pie -o "$OUT/regread-arm" "$BASE/regread.c"
"$STRIP" --strip-all "$OUT/regread-arm"
file "$OUT/regread-host" "$OUT/regread-arm" | tee "$ARCHIVE/build.log"
readelf -h "$OUT/regread-arm" | tee -a "$ARCHIVE/build.log"
readelf -d "$OUT/regread-arm" | tee -a "$ARCHIVE/build.log"
cp "$BASE/regread.c" "$ARCHIVE/"
cp "$BASE/build.sh" "$ARCHIVE/"
cp "$OUT/regread-arm" "$ARCHIVE/"
sha256sum "$BASE/regread.c" "$BASE/build.sh" "$OUT/regread-host" "$OUT/regread-arm" | tee "$ARCHIVE/SHA256SUMS"
printf "ARCHIVE=%s\nARM=%s\nHOST=%s\n" "$ARCHIVE" "$OUT/regread-arm" "$OUT/regread-host"

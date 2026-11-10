#!/usr/bin/env bash
# Host-only ARM32 regression; requires arm-linux-gnueabihf-gcc and qemu-arm.
# Usage: bash tests/run_tests.sh [--source /absolute/HAL.c] [--link-libs /absolute/libs]
# --source allows red/green comparison with the original timestamped backup.
# --link-libs additionally builds a softfp Android candidate against cached image
# libraries. No images are opened, and tracked HAL .so/.o files are never written.
# Z1_AUDIO_PRODUCTS_DIR overrides the existing archive parent; every run creates
# a unique directory containing source/header snapshots, logs and products.
set -euo pipefail
HERE=$(realpath "$(dirname "${BASH_SOURCE[0]}")/..")
WORKSPACE=$(realpath "$HERE/../../..")
SRC="$HERE/audio_primary_z1_poc.c"
LIBDIR=
while (($#)); do
    case "$1" in
        --source|--link-libs)
            (($# >= 2)) || { echo "Missing argument for $1" >&2; exit 2; }
            if [[ "$1" == --source ]]; then SRC=$(realpath "$2"); else LIBDIR=$(realpath "$2"); fi
            shift 2 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done
CC=${CROSS_COMPILE_CC:-arm-linux-gnueabihf-gcc}
QEMU=${QEMU_ARM:-qemu-arm}
SYSROOT=${Z1_TEST_SYSROOT:-/usr/arm-linux-gnueabihf}
PARENT=${Z1_AUDIO_PRODUCTS_DIR:-$WORKSPACE/bridge_backups/products}
for tool in "$CC" "$QEMU" timeout readelf sha256sum; do command -v "$tool" >/dev/null; done
[[ -f "$SRC" && -d "$PARENT" && -d "$SYSROOT" ]] || { echo "Missing source, archive parent or sysroot" >&2; exit 2; }
PARENT=$(realpath "$PARENT")
case "$PARENT/" in "$HERE/"*) echo "Output must be outside tracked audio directory" >&2; exit 2 ;; esac
OUT=$(mktemp -d "$PARENT/products_audio_hal_$(date +%Y%m%d_%H%M%S)_XXXXXX")
printf 'ARTIFACT_DIR=%s\n' "$OUT"
exec > >(tee "$OUT/run.log") 2>&1
set -x
sha256sum "$HERE/libaudio.primary.z1.so" "$HERE/poc_r5g.o" > "$OUT/tracked-binaries.before.sha256"
trap 'sha256sum -c "$OUT/tracked-binaries.before.sha256"' EXIT
cp -p "$SRC" "$OUT/audio_primary_z1_poc.c"
cp -a "$HERE/inc" "$OUT/inc"
cp -a "$HERE/tests" "$OUT/tests"
"$CC" --version
"$QEMU" --version
# The executable uses host ARM hard-float glibc and mocks; ABI asserts remain
# active. The optional Android candidate below is separately compiled softfp.
"$CC" -std=gnu11 -O1 -Wall -Wextra -Werror -Wno-unused-parameter \
    -D_TIME_BITS=32 -I"$OUT/inc" -pthread \
    "$OUT/tests/hal_contract_test.c" "$OUT/audio_primary_z1_poc.c" \
    -o "$OUT/hal_contract_test"
failed=0
for name in retry-null retry-unready retry-long lifecycle volume write-error; do
    if timeout 20 "$QEMU" -L "$SYSROOT" "$OUT/hal_contract_test" "$name"; then
        :
    else
        failed=$((failed + 1))
    fi
done
printf 'SUITE: %d/6 passed\n' "$((6 - failed))"
if ((failed)); then exit 1; fi
if [[ -n "$LIBDIR" ]]; then
    mkdir "$OUT/linklibs"
    for lib in libtinyalsa.so liblog.so libm.so libdl.so libc.so; do
        test -s "$LIBDIR/$lib"
        cp -p "$LIBDIR/$lib" "$OUT/linklibs/$lib"
    done
    LIBDIR="$OUT/linklibs"
    ARCH=(-mcpu=cortex-a7 -mfpu=neon-vfpv4 -mfloat-abi=softfp -mthumb)
    "$CC" -c -fPIC -Os "${ARCH[@]}" -fno-stack-protector -U_FORTIFY_SOURCE \
        -D_TIME_BITS=32 -Wall -Wextra -Werror -Wno-unused-parameter \
        -I"$OUT/inc" -o "$OUT/audio_primary_z1.o" "$OUT/audio_primary_z1_poc.c"
    "$CC" -shared -nostdlib "${ARCH[@]}" \
        -Wl,-soname,libaudio.primary.z1.so -Wl,-z,now -Wl,--no-undefined \
        -o "$OUT/audio.primary.mt6572.so" "$OUT/audio_primary_z1.o" \
        "$LIBDIR/libtinyalsa.so" "$LIBDIR/liblog.so" \
        -Wl,--no-as-needed "$LIBDIR/libm.so" "$LIBDIR/libdl.so" -Wl,--as-needed "$LIBDIR/libc.so"
    readelf -h -A -d "$OUT/audio.primary.mt6572.so" > "$OUT/candidate.readelf.txt"
    readelf --dyn-syms -W "$OUT/audio.primary.mt6572.so" > "$OUT/candidate.dynsyms.txt"
    sha256sum "$OUT/audio.primary.mt6572.so" "$OUT/audio_primary_z1.o" "$OUT"/linklibs/*.so
    echo 'Android candidate built only; no device loading or playback was tested.'
fi
sha256sum "$OUT/audio_primary_z1_poc.c" "$OUT/hal_contract_test" "$OUT"/tests/*
printf 'ARTIFACT_DIR=%s\n' "$OUT"

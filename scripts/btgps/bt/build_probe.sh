#!/usr/bin/env bash
# Build and archive the standalone Z1 MT6572 H4 controller probe.
set -euo pipefail

HERE=$(cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd -P -- "$HERE/../../../.." && pwd)
NDK=${ANDROID_NDK_HOME:-"$ROOT/android-ndk-r26d"}
OUT=${1:-}

[[ -d "$NDK" ]] || {
    echo "Android NDK not found: $NDK" >&2
    exit 2
}
[[ -n "$OUT" ]] || {
    echo "usage: $0 <new output directory>" >&2
    exit 2
}
[[ ! -e "$OUT" ]] || {
    echo "refusing to overwrite output: $OUT" >&2
    exit 2
}

grep -qx 'Pkg.Revision = 26.3.11579264' "$NDK/source.properties" || {
    echo "requires Android NDK r26d (26.3.11579264)" >&2
    exit 2
}

TOOLCHAIN="$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin"
CC="$TOOLCHAIN/armv7a-linux-androideabi28-clang"
READELF="$TOOLCHAIN/llvm-readelf"
[[ -x "$CC" && -x "$READELF" ]] || {
    echo "required NDK compiler or llvm-readelf is missing" >&2
    exit 2
}

WORK=$(mktemp -d "${TMPDIR:-/tmp}/z1-bt-probe.XXXXXXXX")
trap 'find "$WORK" -depth -delete 2>/dev/null || true' EXIT
SOURCE="$HERE/z1_bt_hci_probe.c"
PROBE="$WORK/z1_bt_hci_probe"

[[ -f "$SOURCE" && ! -L "$SOURCE" ]] || {
    echo "probe source is missing or is a symlink: $SOURCE" >&2
    exit 2
}

"$CC" -Wall -Wextra -Werror -std=c11 -O2 -fPIE -pie \
    -Wl,-z,relro,-z,now -o "$PROBE" "$SOURCE"
"$READELF" -h -l -d -n "$PROBE" > "$WORK/readelf.txt"

needed=$(sed -n 's/.*Shared library: \[\([^]]*\)\].*/\1/p' "$WORK/readelf.txt" | sort -u)
[[ "$needed" = $'libc.so\nlibdl.so' ]] || {
    echo "unexpected probe dependencies:" >&2
    printf '%s\n' "$needed" >&2
    exit 3
}
grep -q 'Class:.*ELF32' "$WORK/readelf.txt"
grep -q 'Type:.*DYN' "$WORK/readelf.txt"
grep -q 'Machine:.*ARM' "$WORK/readelf.txt"
grep -q 'interpreter: /system/bin/linker' "$WORK/readelf.txt"
grep -q 'NT_ANDROID_TYPE_IDENT' "$WORK/readelf.txt"
grep -q 'description data: 1c 00 00 00 72 32 36 64 00' "$WORK/readelf.txt"
! grep -q 'TEXTREL' "$WORK/readelf.txt"

mkdir -p "$(dirname -- "$OUT")"
mv "$WORK" "$OUT"
trap - EXIT
cp -- "$SOURCE" "$OUT/z1_bt_hci_probe.c"
cp -- "$HERE/HCI_PROBE.md" "$OUT/HCI_PROBE.md"
printf '%s\n' "$NDK" > "$OUT/ndk-path.txt"
"$CC" --version > "$OUT/compiler.txt"
sha256sum "$OUT/z1_bt_hci_probe" "$OUT/z1_bt_hci_probe.c" \
    "$OUT/HCI_PROBE.md" > "$OUT/SHA256SUMS"

printf 'output=%s\n' "$OUT"
sha256sum "$OUT/z1_bt_hci_probe"

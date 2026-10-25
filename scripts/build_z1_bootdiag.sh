#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
SOURCE="$ROOT/tools/z1_android9_bootdiag.c"
OUTPUT=${1:-"$ROOT/out/z1_bootdiag"}
CC=${Z1_ARM_CC:-arm-linux-gnueabihf-gcc}
READELF=${Z1_ARM_READELF:-arm-linux-gnueabihf-readelf}
FLAGS=(-std=gnu11 -O2 -static -s -Wall -Wextra -Werror
    -march=armv7-a -mfpu=vfpv3-d16 -mfloat-abi=hard
    "-ffile-prefix-map=$ROOT=.")

[[ -s "$SOURCE" ]] || { echo "missing helper source: $SOURCE" >&2; exit 1; }
command -v "$CC" >/dev/null || { echo "missing ARM compiler: $CC" >&2; exit 1; }
command -v "$READELF" >/dev/null || { echo "missing ARM readelf: $READELF" >&2; exit 1; }

mkdir -p "$(dirname "$OUTPUT")"
TEMP=$(mktemp "$(dirname "$OUTPUT")/.z1_bootdiag.XXXXXX")
trap 'rm -f -- "$TEMP"' EXIT

SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH:-0} "$CC" \
    "${FLAGS[@]}" -o "$TEMP" "$SOURCE"

HEADER=$(LC_ALL=C "$READELF" -h "$TEMP")
grep -Eq 'Class:[[:space:]]+ELF32' <<<"$HEADER" || {
    echo "helper is not ELF32" >&2; exit 1;
}
grep -Eq 'Type:[[:space:]]+EXEC' <<<"$HEADER" || {
    echo "helper is not ET_EXEC" >&2; exit 1;
}
grep -Eq 'Machine:[[:space:]]+ARM' <<<"$HEADER" || {
    echo "helper is not ARM" >&2; exit 1;
}
if "$READELF" -l "$TEMP" | grep -q 'INTERP'; then
    echo "helper must not depend on /system/bin/linker" >&2
    exit 1
fi
ATTRIBUTES=$(LC_ALL=C "$READELF" -A "$TEMP")
grep -Eq 'Tag_CPU_arch:[[:space:]]+v7' <<<"$ATTRIBUTES" || {
    echo "helper must target ARMv7-A" >&2; exit 1;
}
grep -q 'Tag_ABI_VFP_args: VFP registers' <<<"$ATTRIBUTES" || {
    echo "helper must use the ARM hard-float ABI" >&2; exit 1;
}
if "$READELF" -d "$TEMP" 2>/dev/null | grep -q '(NEEDED)'; then
    echo "helper must be statically linked with no shared-library dependencies" >&2
    exit 1
fi

chmod 0755 "$TEMP"
mv -f -- "$TEMP" "$OUTPUT"
trap - EXIT
SOURCE_SHA=$(sha256sum "$SOURCE" | cut -d' ' -f1)
BINARY_SHA=$(sha256sum "$OUTPUT" | cut -d' ' -f1)
COMPILER_VERSION=$("$CC" --version | sed -n '1p')
python3 - "$ROOT" "$OUTPUT" "$CC" "$COMPILER_VERSION" "$SOURCE_SHA" "$BINARY_SHA" <<'PY'
import json
from pathlib import Path
import sys

root, output, compiler, compiler_version, source_sha, binary_sha = sys.argv[1:]
source = (Path(root) / "tools/z1_android9_bootdiag.c").relative_to(root).as_posix()
manifest = {
    "source": source,
    "source_sha256": source_sha,
    "compiler": Path(compiler).name,
    "compiler_version": compiler_version,
    "flags": ["-std=gnu11", "-O2", "-static", "-s", "-Wall", "-Wextra",
              "-Werror", "-march=armv7-a", "-mfpu=vfpv3-d16",
              "-mfloat-abi=hard", "-ffile-prefix-map=<repo>=."],
    "target": "ARMv7-A EABI5 hard-float; static ELF32",
    "binary_sha256": binary_sha,
}
Path(output + ".manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
PY
printf '%s  %s\n' "$BINARY_SHA" "$OUTPUT"
echo "static ARM32 helper built: $OUTPUT"

#!/usr/bin/env bash
# Build the R7 Bluetooth direct-H4 fallback system from the verified R7 base.
set -euo pipefail

HERE=$(cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd -P -- "$HERE/../../.." && pwd)
BASE=${Z1_BT_BASE_SYSTEM:-"$ROOT/out/r7_speaker_gpio_candidate_20261119/system.img"}
BASE_SHA=${Z1_BT_BASE_SHA:-21adb51ecb11564fcd65f0f3700469d65b0877e55a7c8fec2d5eec6214571920}
R2_DELTA="$ROOT/out/r7_bt_candidate_20261120_r2/system_from_r7_speaker.img.z1delta"
BASE_DELTA=${Z1_BT_BASE_DELTA:-"$R2_DELTA"}
BASE_DELTA_SHA=${Z1_BT_BASE_DELTA_SHA:-53dab1d7b724d38da356e09c35752e071e1585f53b6071b48552d7a7bcfd832a}
BASE_DELTA_TARGET_SHA=${Z1_BT_BASE_DELTA_TARGET_SHA:-e27601e75d416e6f2986a024a578eac426c9596a4674a31c5d104538cfa67693}
[[ "$BASE_DELTA" != none ]] || BASE_DELTA=
EXPECTED_VENDOR_SHA=a705913c606709447958ac1071366df3d5d5af521a975b29d6cd9815edd0dc7f
TOOL="$HERE/z1_image_block_delta.py"

if [[ $# != 2 ]]; then
    echo "usage: $0 <new-package-dir> <libbt-vendor.so>" >&2
    exit 2
fi

OUT_DIR=$(realpath -m -- "$1")
VENDOR=$2
BOOT=${Z1_BT_BOOT:-"$ROOT/out/r7_bt_candidate_20261120_r2/boot.img"}
BOOT_SHA=${Z1_BT_BOOT_SHA:-4fde12b18f2f0ee7423d63a0165847a0075f2c67148100e77c09d01de08b33bd}
SCATTER=${Z1_BT_SCATTER:-}
SYSTEM="$OUT_DIR/system.img"
DELTA="$OUT_DIR/system_from_base.img.z1delta"

for path in "$BASE" "$VENDOR" "$BOOT" "$TOOL"; do
    [[ -f "$path" && ! -L "$path" ]] || {
        echo "missing regular input: $path" >&2
        exit 2
    }
done
if [[ -n "$BASE_DELTA" ]]; then
    [[ -f "$BASE_DELTA" && ! -L "$BASE_DELTA" ]] || {
        echo "missing regular base delta: $BASE_DELTA" >&2
        exit 2
    }
fi
if [[ -n "$SCATTER" ]]; then
    [[ -f "$SCATTER" && ! -L "$SCATTER" ]] || {
        echo "missing regular scatter file: $SCATTER" >&2
        exit 2
    }
fi
[[ ! -e "$OUT_DIR" && ! -L "$OUT_DIR" ]] || {
    echo "refusing to overwrite output directory: $OUT_DIR" >&2
    exit 2
}

check_sha() {
    local path=$1 expected=$2 actual
    actual=$(sha256sum "$path" | awk '{print $1}')
    [[ "$actual" = "$expected" ]] || {
        echo "sha256 mismatch: $path expected=$expected actual=$actual" >&2
        exit 3
    }
}

check_sha "$BASE" "$BASE_SHA"
check_sha "$BOOT" "$BOOT_SHA"
check_sha "$VENDOR" "$EXPECTED_VENDOR_SHA"

mkdir -p "$OUT_DIR"
if [[ -n "$BASE_DELTA" ]]; then
    check_sha "$BASE_DELTA" "$BASE_DELTA_SHA"
    python3 "$TOOL" apply --base "$BASE" --delta "$BASE_DELTA" --output "$SYSTEM"
    check_sha "$SYSTEM" "$BASE_DELTA_TARGET_SHA"
else
    cp --sparse=always -- "$BASE" "$SYSTEM"
    chmod u+w "$SYSTEM"
fi

debugfs -w -R "rm /vendor/lib/libbt-vendor.so" "$SYSTEM" >/dev/null 2>&1 || true
debugfs -w -R "write $VENDOR /vendor/lib/libbt-vendor.so" "$SYSTEM" >/dev/null
debugfs -w -R "set_inode_field /vendor/lib/libbt-vendor.so mode 0100644" "$SYSTEM" >/dev/null
debugfs -w -R "set_inode_field /vendor/lib/libbt-vendor.so uid 0" "$SYSTEM" >/dev/null
debugfs -w -R "set_inode_field /vendor/lib/libbt-vendor.so gid 0" "$SYSTEM" >/dev/null
for field in mtime atime ctime; do
    debugfs -w -R "set_inode_field /vendor/lib/libbt-vendor.so $field 1780000000" "$SYSTEM" >/dev/null
done
debugfs -w -R "ea_rm /vendor/lib/libbt-vendor.so security.selinux" "$SYSTEM" >/dev/null 2>&1 || true
debugfs -w -R "ea_set /vendor/lib/libbt-vendor.so security.selinux u:object_r:vendor_file:s0" "$SYSTEM" >/dev/null

readback=$(mktemp /tmp/z1-r3-libbt-vendor.XXXXXXXX)
trap 'rm -f -- "$readback"' EXIT
debugfs -R "dump /vendor/lib/libbt-vendor.so $readback" "$SYSTEM" >/dev/null 2>&1
check_sha "$readback" "$EXPECTED_VENDOR_SHA"
debugfs -R "stat /vendor/lib/libbt-vendor.so" "$SYSTEM" 2>/dev/null |
    grep -qE 'Type: regular.*Mode:  0644' || {
        echo "unexpected libbt-vendor inode type or mode" >&2
        exit 3
    }
debugfs -R "ea_list /vendor/lib/libbt-vendor.so" "$SYSTEM" 2>/dev/null |
    grep -q 'u:object_r:vendor_file:s0' || {
        echo "libbt-vendor SELinux label mismatch" >&2
        exit 3
    }

e2fsck -fn "$SYSTEM" >/dev/null
python3 "$TOOL" create --base "$BASE" --target "$SYSTEM" --output "$DELTA"

cp -- "$BOOT" "$OUT_DIR/boot.img"
cp -- "$VENDOR" "$OUT_DIR/libbt-vendor.so"
if [[ -n "$SCATTER" ]]; then
    cp -- "$SCATTER" "$OUT_DIR/MT6572_Z1_R7_STABILITY_scatter.txt"
fi
(
    cd -- "$OUT_DIR"
    files=(boot.img system.img system_from_base.img.z1delta libbt-vendor.so)
    [[ -z "$SCATTER" ]] || files+=(MT6572_Z1_R7_STABILITY_scatter.txt)
    sha256sum "${files[@]}" > SHA256SUMS
)

printf 'package=%s\nsystem_sha256=%s\nsystem_bytes=%s\n' \
    "$OUT_DIR" "$(sha256sum "$SYSTEM" | awk '{print $1}')" "$(stat -c%s "$SYSTEM")"

#!/bin/bash
# Preflight and flash the exact R7 boot/system pair. Dry-run is the default.
set -euo pipefail

HERE=$(cd -P -- "$(dirname -- "$0")" && pwd)
ROOT=$(cd -P -- "$HERE/../../.." && pwd)
CANDIDATE="$ROOT/out/r7_combined_candidate_20261119"
BOOT="$CANDIDATE/boot_hsq_control_r7.img"
SYSTEM="$CANDIDATE/system.img"
BOOT_ROLLBACK="$ROOT/out/r5_offline_candidate_20261119/boot_hsq_control_r5.img"
BOOT_SHA=b2865e43d8be640ae1e9dccbcbc21af60d656cde1a5b9b233264961ae31a3145
SYSTEM_SHA=8060c851075bb053e931376e7965c3c80850a2f5e9d73cdd62e54add83400bf4
EXECUTE=0
CHUNK_MB=48
TRIES=4

usage() {
    cat <<'EOF'
Usage: z1_flash_r7.sh [--execute] [--chunk-mb N] [--tries N]

Without --execute this validates hashes, boot capacity, system filesystem,
and the read-only runtime manifest. --execute writes bootimg first, then the
android partition with checked chunk transfers. No cache or usrdata write is
performed.
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --execute) EXECUTE=1 ;;
        --chunk-mb) CHUNK_MB=${2:?missing chunk size}; shift ;;
        --tries) TRIES=${2:?missing retry count}; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
    shift
done

case "$CHUNK_MB" in ''|*[!0-9]*|0) echo "invalid chunk size" >&2; exit 2 ;; esac
case "$TRIES" in ''|*[!0-9]*|0) echo "invalid retry count" >&2; exit 2 ;; esac

for path in "$BOOT" "$SYSTEM" "$BOOT_ROLLBACK" \
    "$ROOT/tools/flash_with_retry.sh" "$ROOT/tools/z1_flash_chunked.sh"; do
    [ -f "$path" ] || { echo "missing: $path" >&2; exit 2; }
done

check_sha() {
    path=$1
    expected=$2
    actual=$(sha256sum "$path" | awk '{print $1}')
    [ "$actual" = "$expected" ] || {
        echo "SHA256 mismatch: $path expected=$expected actual=$actual" >&2
        exit 3
    }
}

check_sha "$BOOT" "$BOOT_SHA"
check_sha "$SYSTEM" "$SYSTEM_SHA"
(cd "$CANDIDATE" && sha256sum -c SHA256SUMS >/dev/null) ||
    { echo "candidate SHA256SUMS failed" >&2; exit 3; }

boot_size=$(stat -c%s "$BOOT")
[ "$boot_size" -le $((6 * 1024 * 1024)) ] ||
    { echo "boot exceeds 6 MiB" >&2; exit 3; }
[ "$(head -c 8 "$BOOT")" = ANDROID! ] ||
    { echo "boot lacks ANDROID! magic" >&2; exit 3; }
[ "$(stat -c%s "$SYSTEM")" -eq 943718400 ] ||
    { echo "unexpected system image size" >&2; exit 3; }
e2fsck -fn "$SYSTEM" >/dev/null ||
    { echo "system e2fsck failed" >&2; exit 3; }

debugfs -R 'cat /bin/z1_runtime_manifest' "$SYSTEM" 2>/dev/null |
    grep -F 'r7-combined-20261119' >/dev/null ||
    { echo "R7 runtime manifest is not installed" >&2; exit 3; }
for pair in \
    "/lib/modules/z1-radio/wlan_gen2.ko 143c5611267eb4c5e20135fffdd39db491753147ef295278c3a69330c1fbf80f" \
    "/lib/modules/z1-radio/z1_gpsdrv.ko 5cab8a98e9bad3fc7cfa369af2b850dc9c271cd81678150b77279d22667de717" \
    "/vendor/lib/hw/audio.primary.default.so 3f1b3a841aea4eedd7cb7ba5c22ebe12e6062b6ec88ec5d28eed6864251e1722"; do
    read -r payload expected <<< "$pair"
    actual=$(debugfs -R "cat $payload" "$SYSTEM" 2>/dev/null | sha256sum)
    actual=${actual%% *}
    [ "$actual" = "$expected" ] ||
        { echo "image payload mismatch: $payload expected=$expected actual=$actual" >&2; exit 3; }
done

echo "R7 preflight PASS"
echo "  boot:   $BOOT ($boot_size bytes, sha256 $BOOT_SHA)"
echo "  system: $SYSTEM (sha256 $SYSTEM_SHA)"
echo "  target: bootimg then android; rollback boot $BOOT_ROLLBACK"
if [ "$EXECUTE" -ne 1 ]; then
    echo "dry-run only; rerun with --execute when the device is ready"
    exit 0
fi

echo "=== Flash 1/2: bootimg ==="
Z1_VERIFY=1 "$ROOT/tools/flash_with_retry.sh" \
    bootimg "$BOOT" "$BOOT_ROLLBACK" 8

echo "=== Flash 2/2: android (verified chunks) ==="
"$ROOT/tools/z1_flash_chunked.sh" android "$SYSTEM" "$CHUNK_MB" "$TRIES"

echo "R7 flash and readback verification completed"
echo "Unplug the download cable and start UART capture before powering on."

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
R7_BASE_BOOT_SHA=$BOOT_SHA
HWC_BLANK_BOOT="$ROOT/out/r7_lockscreen_restart_20261120/boot.img"
HWC_BLANK_SHA=a7e5b0bbc33f13a35c676f84f1a103a9a7d3b5e0e77c65e8f6f25108305115e7
HWC_BLANK_ROLLBACK="$ROOT/out/r7_hwc_blank_candidate_20261119/boot.img"
HWC_BLANK_ROLLBACK_SHA=bb6b1b6711036146607018dec27d04538bbe54e0d5696f72b0f110ba31cbebed
SYSTEM_SHA=8060c851075bb053e931376e7965c3c80850a2f5e9d73cdd62e54add83400bf4
EXECUTE=0
HWC_BLANK=0
SPEAKER_GPIO=0
CHUNK_MB=48
TRIES=4

usage() {
    cat <<'EOF'
Usage: z1_flash_r7.sh [--execute] [--hwc-blank|--speaker-gpio] [--chunk-mb N] [--tries N]

Without --execute this validates hashes, boot capacity, system filesystem,
and the read-only runtime manifest. --execute writes bootimg first, then the
android partition with checked chunk transfers. No cache or usrdata write is
performed.
--hwc-blank selects the verified boot with the restart-safe backlight gate fix.
--speaker-gpio selects the paired R7 system with the GPIO140 speaker-enable
machine module and matching appended DTB.
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --execute) EXECUTE=1 ;;
        --hwc-blank) HWC_BLANK=1 ;;
        --speaker-gpio) SPEAKER_GPIO=1 ;;
        --chunk-mb) CHUNK_MB=${2:?missing chunk size}; shift ;;
        --tries) TRIES=${2:?missing retry count}; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
    shift
done

case "$CHUNK_MB" in ''|*[!0-9]*|0) echo "invalid chunk size" >&2; exit 2 ;; esac
case "$TRIES" in ''|*[!0-9]*|0) echo "invalid retry count" >&2; exit 2 ;; esac

if [ "$HWC_BLANK" -eq 1 ] && [ "$SPEAKER_GPIO" -eq 1 ]; then
    echo "--hwc-blank and --speaker-gpio select different boots" >&2
    exit 2
fi

if [ "$SPEAKER_GPIO" -eq 1 ]; then
    CANDIDATE="$ROOT/out/r7_speaker_gpio_candidate_20261119"
    BOOT="$CANDIDATE/boot.img"
    SYSTEM="$CANDIDATE/system.img"
    BOOT_SHA=e761299886d61bd5cfca6ff0876cf36d0355a3216c7cbb050a9cf08016f56a1d
    BOOT_ROLLBACK=$HWC_BLANK_BOOT
    SYSTEM_SHA=21adb51ecb11564fcd65f0f3700469d65b0877e55a7c8fec2d5eec6214571920
fi

if [ "$HWC_BLANK" -eq 1 ]; then
    BOOT_ROLLBACK=$HWC_BLANK_ROLLBACK
    BOOT=$HWC_BLANK_BOOT
    BOOT_SHA=$HWC_BLANK_SHA
fi

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
if [ "$HWC_BLANK" -eq 1 ]; then
    check_sha "$BOOT_ROLLBACK" "$HWC_BLANK_ROLLBACK_SHA"
elif [ "$SPEAKER_GPIO" -eq 1 ]; then
    check_sha "$BOOT_ROLLBACK" "$HWC_BLANK_SHA"
fi
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

if [ "$SPEAKER_GPIO" -eq 1 ]; then
    PREFLIGHT_TMP=$(mktemp -d /tmp/z1-speaker-preflight.XXXXXX)
    trap 'rm -rf "$PREFLIGHT_TMP"' EXIT

    python3 "$ROOT/z1a9_gpt/scripts/bootspeed/bootimg_tool.py" \
        split "$BOOT" "$PREFLIGHT_TMP" >/dev/null

    fdtget=$(command -v fdtget) ||
        { echo "fdtget is required for speaker preflight" >&2; exit 3; }
    pio_phandle=$("$fdtget" -t x "$PREFLIGHT_TMP/dtb" \
        /soc/pinctrl@1000b000 phandle 2>/dev/null) ||
        { echo "cannot read pinctrl phandle" >&2; exit 3; }
    read -r gpio_phandle gpio_number gpio_flags <<< "$(
        "$fdtget" -t x "$PREFLIGHT_TMP/dtb" /sound speaker-enable-gpios 2>/dev/null
    )" || true
    [ "${gpio_phandle:-}" = "$pio_phandle" ] &&
    [ "${gpio_number:-}" = 8c ] &&
    [ "${gpio_flags:-}" = 0 ] ||
        { echo "speaker-enable-gpios is not GPIO140 active-high" >&2; exit 3; }

    initrd_start_hex=$("$fdtget" -t x "$PREFLIGHT_TMP/dtb" \
        /chosen linux,initrd-start 2>/dev/null) ||
        { echo "cannot read linux,initrd-start" >&2; exit 3; }
    initrd_end_hex=$("$fdtget" -t x "$PREFLIGHT_TMP/dtb" \
        /chosen linux,initrd-end 2>/dev/null) ||
        { echo "cannot read linux,initrd-end" >&2; exit 3; }
    initrd_start=$((16#$initrd_start_hex))
    initrd_end=$((16#$initrd_end_hex))
    rootfs_bytes=$(stat -c%s "$PREFLIGHT_TMP/rootfs.img")
    rootfs_body_bytes=$((rootfs_bytes - 512))
    [ "$initrd_start" -eq $((0x84100000)) ] &&
    [ $((initrd_end - initrd_start)) -eq "$rootfs_body_bytes" ] ||
        { echo "DTB initrd range does not match the boot ramdisk" >&2; exit 3; }

    rootfs_magic=$(od -An -tx1 -N16 "$PREFLIGHT_TMP/rootfs.img" | tr -d ' \n')
    [ "$rootfs_magic" = 88168858550d1900524f4f5446530000 ] ||
        { echo "boot ramdisk lacks the MTK ROOTFS header" >&2; exit 3; }
    dd if="$PREFLIGHT_TMP/rootfs.img" of="$PREFLIGHT_TMP/body.gz" \
        bs=512 skip=1 status=none
    gzip -t "$PREFLIGHT_TMP/body.gz" ||
        { echo "boot ramdisk body is not valid gzip" >&2; exit 3; }
    gzip -dc "$PREFLIGHT_TMP/body.gz" |
        cpio -i --to-stdout init.z1.rc 2>/dev/null |
        grep -F \
        'exec_background u:r:init:s0 root root -- /system/bin/sh /system/lib/modules/load_audio.sh' \
        >/dev/null ||
        { echo "ramdisk init does not invoke the system audio loader" >&2; exit 3; }

    module_path=/lib/modules/mt6572-mt6323.ko
    module_sha=23977c4fdcf139ca879161dec63add016c304d77e8a3b47569db868755716dd3
    module_stat=$(debugfs -R "stat $module_path" "$SYSTEM" 2>/dev/null) ||
        { echo "cannot stat $module_path" >&2; exit 3; }
    grep -q 'Type: regular' <<< "$module_stat" &&
    grep -Eq 'Mode:[[:space:]]+0664' <<< "$module_stat" &&
    grep -Eq 'User:[[:space:]]+0[[:space:]]+Group:[[:space:]]+0' <<< "$module_stat" &&
    grep -F 'u:object_r:system_file:s0' <<< "$module_stat" >/dev/null ||
        { echo "$module_path metadata mismatch" >&2; exit 3; }
    actual=$(debugfs -R "cat $module_path" "$SYSTEM" 2>/dev/null | sha256sum)
    actual=${actual%% *}
    [ "$actual" = "$module_sha" ] ||
        { echo "$module_path SHA256 mismatch: expected=$module_sha actual=$actual" >&2; exit 3; }

    loader=$(debugfs -R 'cat /lib/modules/load_audio.sh' "$SYSTEM" 2>/dev/null)
    grep -F 'BASE=/system/lib/modules' <<< "$loader" >/dev/null &&
    grep -F 'load mt6572-mt6323.ko    mt6572_mt6323' <<< "$loader" >/dev/null ||
        { echo "audio loader does not load the speaker machine module" >&2; exit 3; }

fi

if [ "$SPEAKER_GPIO" -eq 1 ]; then
    echo "R7 speaker GPIO preflight PASS"
else
    echo "R7 preflight PASS"
fi
echo "  boot:   $BOOT ($boot_size bytes, sha256 $BOOT_SHA)"
echo "  system: $SYSTEM (sha256 $SYSTEM_SHA)"
echo "  target: bootimg then android; rollback boot $BOOT_ROLLBACK"
if [ "$SPEAKER_GPIO" -eq 1 ]; then
    echo "  speaker: GPIO140 active-high, module $module_sha"
fi
if [ "$EXECUTE" -ne 1 ]; then
    echo "dry-run only; add --execute to this command when the device is ready"
    exit 0
fi

echo "=== Flash 1/2: bootimg ==="
Z1_VERIFY=1 "$ROOT/tools/flash_with_retry.sh" \
    bootimg "$BOOT" "$BOOT_ROLLBACK" 8

echo "=== Flash 2/2: android (verified chunks) ==="
"$ROOT/tools/z1_flash_chunked.sh" android "$SYSTEM" "$CHUNK_MB" "$TRIES"

echo "R7 flash and readback verification completed"
echo "Unplug the download cable and start UART capture before powering on."

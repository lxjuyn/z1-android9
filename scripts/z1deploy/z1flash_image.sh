#!/bin/bash
# ---------------------------------------------------------------------------
# z1flash_image.sh -- write ONE partition image to the Z1 over MTK preloader,
# with read-back verification and a bounded retry window.
#
#   ./z1flash_image.sh <image> [--offset 0x2980000] [--minutes 10]
#
# Why this exists (and why it is careful):
#   * mtkclient occasionally half-writes large images on this old MT6572, so
#     every successful write is followed by a full read-back and a byte-for-byte
#     compare.  A mismatch is reported as a FAILURE, never as success.
#   * the boot partition is 6 MiB; an image larger than that is refused before
#     anything is written.
#   * the board only answers in preloader/BROM mode: unplug USB, start this
#     script, then plug USB + power on.  Unplug USB again for a normal boot
#     (the preloader inspects USB LineState and would enter download mode).
#   * a lock file prevents two flashers from fighting each other (this has
#     bitten us: an old polling loop was still writing a stale image).
#
# Defaults match the Z1 boot partition (GPT "bootimg", byte offset 0x2980000).
# Partition table (from the forensic dump):
#   preloader 0x0            uboot 0x60000        bootimg 0x2980000 (6 MiB)
#
# Exit codes:
#   0 image written and read back byte-identical
#   2 no preloader window inside the time budget
#   4 write acknowledged but read-back differs (image may be half written)
#   5 refused (image too large / not a file / another flasher holds the lock)
# ---------------------------------------------------------------------------
set -u

HERE=$(cd -P -- "$(dirname -- "$0")" && pwd)
MTK_DIR=${Z1_MTK_DIR:-/home/lxj/claude/6572/mtkclient_v2141}
MTK_PY=${Z1_MTK_PY:-/home/lxj/claude/6572/mtkclient_venv/bin/python}
SUDO_PW=${Z1_SUDO_PW:-1}
LOCK=${Z1_FLASH_LOCK:-/tmp/z1flash.lock}
IMG=""
OFFSET=${Z1_BOOT_OFFSET:-0x2980000}
MINUTES=10
MIN_BYTES=$((6 * 1024 * 1024))

while [ $# -gt 0 ]; do
    case "$1" in
        --offset)  OFFSET=$2; shift ;;
        --minutes) MINUTES=$2; shift ;;
        -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
        -*) echo "unknown option: $1" >&2; exit 5 ;;
        *)  IMG=$1 ;;
    esac
    shift
done

die() { printf 'z1flash: %s\n' "$*" >&2; exit 5; }

[ -n "$IMG" ] || die "usage: $0 <image> [--offset 0x...] [--minutes N]"
[ -f "$IMG" ] || die "no such file: $IMG"
[ -x "$MTK_PY" ] || die "mtkclient python not found: $MTK_PY"
[ -f "$MTK_DIR/mtk.py" ] || die "mtk.py not found in $MTK_DIR"
[ -d "$MTK_DIR" ] || die "mtkclient dir not found: $MTK_DIR"

SIZE=$(stat -c%s "$IMG")
[ "$SIZE" -le "$MIN_BYTES" ] || die "$IMG is $SIZE bytes > 6 MiB boot partition"
SIZE_HEX=$(printf '0x%x' "$SIZE")
SHA=$(sha256sum "$IMG" | awk '{print $1}')

exec 9>"$LOCK"
flock -n 9 || die "another flash is already running (lock $LOCK)"

echo "z1flash: image   $IMG"
echo "z1flash: size    $SIZE bytes ($SIZE_HEX), sha256 $SHA"
echo "z1flash: offset  $OFFSET"
echo "z1flash: budget  $MINUTES minute(s)"
echo
echo "Now: unplug the Z1 USB cable, then plug it back in (or power-cycle the"
echo "board while USB is connected) so the preloader shows up."
echo

READBACK=/tmp/z1flash_readback_$$.bin
trap 'rm -f "$READBACK"' EXIT

TRIES=$((MINUTES * 12))          # one attempt every 5 s
for t in $(seq 1 "$TRIES"); do
    WRITE_LOG=/tmp/z1flash_write_$$.log
    echo "$SUDO_PW" | sudo -S timeout 60 "$MTK_PY" "$MTK_DIR/mtk.py" wo "$OFFSET" "$SIZE_HEX" "$IMG" \
        > "$WRITE_LOG" 2>&1
    if grep -q "Wrote .* to offset .* with length" "$WRITE_LOG"; then
        echo "z1flash: write acknowledged on attempt $t"
        rm -f "$READBACK"
        echo "$SUDO_PW" | sudo -S timeout 180 "$MTK_PY" "$MTK_DIR/mtk.py" ro "$OFFSET" "$SIZE_HEX" "$READBACK" \
            > /tmp/z1flash_read_$$.log 2>&1
        RSIZE=$(stat -c%s "$READBACK" 2>/dev/null || echo 0)
        if [ "$RSIZE" != "$SIZE" ]; then
            echo "z1flash: read-back size $RSIZE != $SIZE -- treat as FAILURE" >&2
            rm -f "$WRITE_LOG"
            exit 4
        fi
        if cmp -s "$IMG" "$READBACK"; then
            echo "z1flash: read-back byte-identical ($(sha256sum "$READBACK" | awk '{print $1}'))"
            rm -f "$WRITE_LOG"
            echo
            echo "Unplug the USB cable and power-cycle the board for a normal boot."
            exit 0
        fi
        echo "z1flash: read-back DIFFERS from the image -- FAILURE" >&2
        echo "         first differing byte: $(cmp "$IMG" "$READBACK" 2>&1 | head -1)" >&2
        rm -f "$WRITE_LOG"
        exit 4
    fi
    rm -f "$WRITE_LOG"
    [ $((t % 12)) -eq 0 ] && echo "z1flash: still waiting ($((t * 5)) s elapsed)"
    sleep 5
done

echo "z1flash: no preloader window within $MINUTES minute(s); nothing written" >&2
exit 2

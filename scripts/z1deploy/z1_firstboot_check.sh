#!/bin/bash
# ---------------------------------------------------------------------------
# z1_firstboot_check.sh -- one-shot evidence collector for the first boot of
# the v83 image (or any later image that carries the z1deploy channel).
#
# It only READS from the device: it sends a fixed list of diagnostic commands
# over the serial console shell (uid 2000) plus one root block, and writes
# everything to a timestamped report so an analyser does not need the board.
#
# Usage:
#   Z1LOG=/path/to/append-only-capture.log ./z1_firstboot_check.sh [outfile]
#
# Prerequisites:
#   * a capture process is appending the serial console to $Z1LOG
#       ( e.g. nohup /tmp/z1cap.sh /home/lxj/claude/6572/mainline_recon/z1_boot_live.log & )
#   * the board has booted far enough for the `console:/ $` shell to answer
#   * the device is powered (this script never flashes and never reboots)
#
# Exit status: 0 if every block was collected, 1 if some block timed out
# (the report is written either way).
# ---------------------------------------------------------------------------
set -u

HERE=$(cd -P -- "$(dirname -- "$0")" && pwd)
PUSH="$HERE/z1push.sh"
DEV=${Z1DEV:-/dev/ttyUSB0}
LOG=${Z1LOG:?set Z1LOG to the append-only serial capture file}
OUT=${1:-$HERE/../../mainline_recon/z1_firstboot_$(date +%Y%m%d_%H%M%S).txt}
WAIT=${Z1_WAIT:-20}

[ -x "$PUSH" ] || { echo "missing $PUSH" >&2; exit 2; }
[ -r "$LOG" ] || { echo "capture file $LOG is not readable" >&2; exit 2; }

export Z1DEV="$DEV" Z1LOG="$LOG"

mkdir -p "$(dirname -- "$OUT")"
: >"$OUT"
FAILED=0

say() { printf '%s\n' "$*" | tee -a "$OUT"; }

run() {   # run <label> <command>
    say ""
    say "===== $1 ====="
    if "$PUSH" --cmd "$2" --wait "$WAIT" >>"$OUT" 2>&1; then
        say "----- $1: OK -----"
    else
        say "----- $1: TIMEOUT/FAIL (device shell may be busy) -----"
        FAILED=$((FAILED + 1))
    fi
}

runroot() {   # runroot <label> <multi-line script>
    say ""
    say "===== root: $1 ====="
    if printf '%s\n' "$2" | "$PUSH" --rootcmd - --wait "$WAIT" >>"$OUT" 2>&1; then
        say "----- root: $1: OK -----"
    else
        say "----- root: $1: TIMEOUT/FAIL (is z1_rootd running?) -----"
        FAILED=$((FAILED + 1))
    fi
}

say "Z1 first-boot evidence  $(date -Is)"
say "serial=$DEV  capture=$LOG"
say ""

# --- boot / property state -------------------------------------------------
run props 'getprop sys.boot_completed; getprop ro.hardware; getprop ro.product.board; getprop ro.hardware.egl; getprop z1stage.hwcomposer; getprop z1stage.gralloc; getprop debug.z1.hwc.verbose; getprop debug.z1.hwc.mode; getprop init.svc.vendor.hwcomposer-2-1; getprop init.svc.z1_rootd'

# --- nodes ---------------------------------------------------------------
run nodes 'ls -l /dev/dri 2>&1; ls -l /dev/dma_heap 2>&1; ls -l /dev/graphics 2>&1'

# --- which HAL is installed ---------------------------------------------
run vendorlibs 'ls -l /vendor/lib/hw/ 2>&1 | grep -E "hwcomposer|gralloc"; sha256sum /vendor/lib/hw/hwcomposer.z1.so /system/lib/hw/hwcomposer.z1.so /vendor/lib/hw/gralloc.z1.so /system/lib/hw/gralloc.z1.so 2>&1'

# --- processes -----------------------------------------------------------
run procs 'ps -A -o PID,USER,NAME 2>/dev/null | grep -E "surfaceflinger|hwcomposer|gralloc|z1_rootd" | head -20'

# --- deployment channel log ---------------------------------------------
run deploylog 'dmesg | grep -a -E "z1stage:|z1rootd:" | tail -40'

# --- HAL log -------------------------------------------------------------
run hwclog 'dmesg | grep -a -c "z1hwc2"; dmesg | grep -a "z1hwc2" | tail -60'

# --- composer / SurfaceFlinger ------------------------------------------
run composer 'logcat -d -b main -b system -t 4000 2>/dev/null | grep -a -E "ComposerHal|hwcomposer|HWC2|Could not get passthrough" | tail -40'

run sfstart 'logcat -d -b events -t 4000 2>/dev/null | grep -a -E "boot_progress|sf_stop_bootanim|am_crash" | tail -25'

# --- composition types ---------------------------------------------------
run sfdump 'dumpsys SurfaceFlinger 2>/dev/null | head -70'

run sfcomp 'dumpsys SurfaceFlinger 2>/dev/null | grep -a -iE "composition|z1hwc2|Display .* HWC" | head -40'

# --- kernel display state (may need root) --------------------------------
run kms 'cat /sys/kernel/debug/dri/1/state 2>/dev/null | head -60; cat /proc/interrupts 2>/dev/null | grep -iE "ovl|rdma|dsi"'

# --- root-only detail ----------------------------------------------------
runroot dma_heap 'ls -l /dev/dma_heap 2>&1
ls /sys/class/dma_heap 2>&1
for f in /sys/class/dma_heap/*/dev; do echo "$f: $(cat $f 2>/dev/null)"; done
cat /proc/cmdline
ls -l /vendor/lib/hw/ | grep -E "hwcomposer|gralloc"'

runroot kmsstate 'cat /sys/kernel/debug/dri/1/state 2>/dev/null | head -80
echo "--- sw_sync ---"
ls -l /sys/kernel/debug/sync 2>/dev/null
echo "--- dma-buf heaps in dmesg ---"
dmesg | grep -a -iE "dma_heap|cma:" | head -10'

# --- automatic verdict -----------------------------------------------------
# Turns the raw dump into the handful of facts that decide the next step.
say ""
say "===== VERDICT ====="
v() {   # v <label> <pattern> <present|absent|count>
    local label=$1 pat=$2 want=$3 n
    n=$(grep -a -c -- "$pat" "$OUT" 2>/dev/null || true)
    case "$want" in
        present) [ "${n:-0}" -gt 0 ] && say "PASS  $label" || say "FAIL  $label (no match for /$pat/)" ;;
        absent)  [ "${n:-0}" -eq 0 ] && say "PASS  $label" || say "FAIL  $label ($n matches for /$pat/)" ;;
        count)   say "INFO  $label: $n occurrence(s) of /$pat/" ;;
    esac
}

v "root channel alive (z1_rootd)"            "z1rootd:" present
v "deploy staging ran (z1stage:)"            "z1stage:" present
v "HWC auto-installed on first boot"          "auto-install: seeded active" present
v "dma_heap node created"                     "mknod /dev/dma_heap" present
v "HAL opened card1"                          "z1hwc2: opened /dev/dri/card1" present
v "HAL is DRM master"                         "not DRM master" absent
v "first atomic commit accepted"              "first commit" present
v "plane-only fallback used"                  "plane-only commit accepted" count
v "atomic commit failures"                    "atomic commit failed" count
v "CPU copy fallback in use (no contiguous)"  "client target COPIED" count
v "HAL crashed/restarted"                     "failed to get hwcomposer2" absent
v "SurfaceFlinger has a composer"             "Waited one second for .*IComposer" absent
v "boot completed"                            "sys.boot_completed" count
v "composer service running"                  "vendor.hwcomposer-2-1=running" present

say ""
if [ "$FAILED" -eq 0 ]; then
    say "RESULT: all blocks collected -> $OUT"
else
    say "RESULT: $FAILED block(s) failed -> $OUT (report still useful)"
fi
exit $((FAILED > 0))

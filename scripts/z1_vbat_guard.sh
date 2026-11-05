#!/bin/bash
# Report the last VBAT the Z1 preloader printed, and whether it is safe to flash.
#
# Why: on 2026-11-05 three consecutive 943 MiB system writes died with
#   "Data ack failed for sdmmc_write_data" / USBError(5)
# at 86.8%, 22.2% and 19.6%.  The preloader log said:
#   check VBAT=3034 mV with 3450 mV
# i.e. the pack was at 3.03 V and the eMMC browned out under write current.
# Flashing at that level cannot succeed and the "rollback" write fails the same
# way, so the wrapper spins for hours.  Check this first.
set -u
LOG_DIR=/home/lxj/claude/6572/mainline_recon
MIN_MV="${1:-3600}"      # below this, do not flash
LOG=$(ls -t "$LOG_DIR"/z1_boot_mainline_*.log 2>/dev/null | head -1)
[ -n "$LOG" ] || { echo "NO_LOG: no capture in $LOG_DIR"; exit 2; }

last=$(grep -ahoE 'check VBAT=[0-9]+ mV' "$LOG" 2>/dev/null | tail -1)
if [ -z "$last" ]; then
    echo "NO_VBAT: newest capture ($(basename "$LOG")) has no 'check VBAT=' line"
    echo "         -> preloader did not run in this capture; power-cycle once and retry"
    exit 2
fi
mv=$(echo "$last" | grep -oE '[0-9]+')
echo "log=$(basename "$LOG")  ${last}  threshold=${MIN_MV}mV"
if [ "$mv" -lt "$MIN_MV" ]; then
    echo "REFUSE: ${mv} mV < ${MIN_MV} mV -- charge first; flashing now will brown out mid-write"
    exit 1
fi
echo "OK: ${mv} mV >= ${MIN_MV} mV"
exit 0

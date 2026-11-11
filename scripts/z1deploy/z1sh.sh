#!/usr/bin/env bash
# =============================================================================
#  z1sh.sh — send ONE line to the Z1 console shell and print what the shell
#            answered (kernel console noise stripped).
#
#  The console is a getty on the S3 UART.  Two things break naive usage:
#    * Kernel printk lines are interleaved with shell output on the same wire.
#      They start with "[<seconds>.<usec>]" and are dropped here.
#    * Sustained input overruns the UART ("ttyS ttyS0: N input overrun(s)") and
#      the shell then only echoes characters without executing anything.
#      Keep commands SHORT and do not blast large payloads through this path.
#
#  Usage:
#    z1sh.sh '<command>'                 # LOG/DEV/WAIT from the environment
#  Environment:
#    Z1LOG    log file the capture writes to (default: ./z1_serial.log)
#    Z1DEV    serial device (default: /dev/ttyUSB0)
#    Z1WAIT   seconds to wait for the answer (default: 5)
#    Z1TAIL   how many trailing non-kernel lines to print (default: 20)
# =============================================================================
set -u

[ $# -ge 1 ] || { printf 'usage: %s <command>\n' "$0" >&2; exit 2; }

DEV=${Z1DEV:-/dev/ttyUSB0}
LOG=${Z1LOG:-$PWD/z1_serial.log}
WAIT=${Z1WAIT:-5}
TAIL=${Z1TAIL:-20}

[ -c "$DEV" ] || { printf 'z1sh: %s is not a character device\n' "$DEV" >&2; exit 3; }
[ -f "$LOG" ] || { printf 'z1sh: capture log %s does not exist (start z1cap.sh first)\n' "$LOG" >&2; exit 4; }

OFF=$(stat -c%s "$LOG" 2>/dev/null || echo 0)
printf '%s\r' "$1" > "$DEV" || { printf 'z1sh: write to %s failed\n' "$DEV" >&2; exit 5; }
sleep "$WAIT"

tail -c +$((OFF + 1)) "$LOG" 2>/dev/null \
    | tr -d '\r' \
    | grep -v '^\[ *[0-9]*\.[0-9]*\]' \
    | grep -v '^<z1cap:' \
    | grep -v '^[[:space:]]*$' \
    | tail -n "$TAIL"

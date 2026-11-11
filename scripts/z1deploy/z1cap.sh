#!/usr/bin/env bash
# =============================================================================
#  z1cap.sh — capture the Z1 serial console into a log file.
#
#  Why this exists (and why it is in the repo, not /tmp):
#    * /tmp on this host gets wiped, which silently removes debugging helpers.
#    * The CH340 re-enumerates whenever the cable is moved, and a freshly
#      enumerated ttyUSB* comes back at 9600 baud.  Reading a 921600 board at
#      9600 produces pure binary garbage, which looks exactly like "the board
#      is dead".  So the baud rate is re-applied whenever the device (re)appears.
#
#  Usage:
#    z1cap.sh [logfile] [device] [baud]
#      logfile  default: <cwd>/z1_serial.log
#      device   default: /dev/ttyUSB0
#      baud     default: 921600
#
#  Run it with setsid so it survives the shell that started it:
#    setsid nohup scripts/z1deploy/z1cap.sh /path/to/log >/dev/null 2>&1 </dev/null &
# =============================================================================
set -u

LOG=${1:-$PWD/z1_serial.log}
DEV=${2:-/dev/ttyUSB0}
SPEED=${3:-921600}

mkdir -p "$(dirname "$LOG")" 2>/dev/null || true

while true; do
    if [ -c "$DEV" ]; then
        cur=$(stty -F "$DEV" -a 2>/dev/null | head -1 | sed -n 's/.*speed \([0-9]*\) baud.*/\1/p')
        if [ "$cur" != "$SPEED" ]; then
            if stty -F "$DEV" "$SPEED" cs8 -cstopb -parenb -crtscts -echo -icanon \
                       -ixon -ixoff raw 2>/dev/null; then
                printf '<z1cap: %s set to %s baud>\n' "$DEV" "$SPEED" >> "$LOG"
            fi
        fi
        cat "$DEV" >> "$LOG" 2>/dev/null
    fi
    sleep 1
done

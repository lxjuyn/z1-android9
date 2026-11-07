#!/system/bin/sh
# z1_bt_diag.sh - one-shot Bluetooth bisect dump for the Z1 (Android 9 / LOS16).
# Runs fine as root or as the serial 'shell' user (uid 2000).
log() { printf '<6>[z1btdiag] %s\n' "$*" > /dev/kmsg 2>/dev/null || true; }

log "=== bluetooth diag begin uptime=$(cut -d. -f1 /proc/uptime 2>/dev/null)s ==="

log "--- properties ---"
/system/bin/getprop 2>/dev/null | grep -iE 'bluetooth|btnvram|bdroid|ro\.bt\.' \
  | while IFS= read -r l; do log "prop $l"; done

log "--- settings (the bluetooth_on row is the decisive one) ---"
log "global bluetooth_on = $(/system/bin/settings get global bluetooth_on 2>&1)"
log "secure bluetooth_addr_valid = $(/system/bin/settings get secure bluetooth_addr_valid 2>&1)"
log "secure bluetooth_name = $(/system/bin/settings get secure bluetooth_name 2>&1)"
log "secure bluetooth_address = $(/system/bin/settings get secure bluetooth_address 2>&1)"

log "--- binder ---"
log "$(/system/bin/service check bluetooth_manager 2>&1)"
log "--- dumpsys bluetooth_manager (head 80) ---"
/system/bin/dumpsys bluetooth_manager 2>&1 | head -80 \
  | while IFS= read -r l; do log "dump $l"; done

log "--- processes ---"
/system/bin/ps -A 2>/dev/null | grep -iE 'bluetooth|bt_' \
  | while IFS= read -r l; do log "ps $l"; done

log "--- package state ---"
log "pm list packages -d: $(/system/bin/pm list packages -d 2>/dev/null | grep -i bluetooth)"
log "pm list packages:    $(/system/bin/pm list packages 2>/dev/null | grep -i bluetooth)"
log "pm path: $(/system/bin/pm path com.android.bluetooth 2>&1)"

log "--- logcat history (D and above, filtered) ---"
/system/bin/logcat -d -b main -b system -b crash -v threadtime 2>/dev/null \
  | grep -aE 'BluetoothManagerService|BluetoothAdapter|AdapterService|BluetoothHci|BluetoothService|bt_vendor|bt_stack|btif|bt_hci|Btservice' \
  | tail -150 | while IFS= read -r l; do log "log $l"; done

log "--- kernel stpbt/btif ---"
/system/bin/dmesg 2>/dev/null | grep -aiE 'stpbt|BT_open|btif|stp_wmt|stp_bt|wmt' \
  | tail -40 | while IFS= read -r l; do log "kern $l"; done

log "--- nodes / selinux ---"
ls -l /dev/stpbt /dev/stpwmt 2>&1 | while IFS= read -r l; do log "dev $l"; done
log "selinux: $(/system/bin/getprop ro.boot.selinux)"

log "=== bluetooth diag end ==="

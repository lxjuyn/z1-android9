#!/system/bin/sh
# z1_bt_diag.sh - one-shot Bluetooth bisect dump for the Z1 (Android 9 / LOS16).
# Runs fine as root or as the serial 'shell' user (uid 2000).
# Deliberately does not print the local adapter address or the Bluetooth name.
log() { printf '<6>[z1btdiag] %s\n' "$*" > /dev/kmsg 2>/dev/null || true; }
# Drop lines that could contain the local address or device name.
redact() { grep -avEi 'bluetooth_address|bluetooth_name|address:|BD_ADDR|bdaddr|"name"'; }

log "=== bluetooth diag begin uptime=$(cut -d. -f1 /proc/uptime 2>/dev/null)s ==="

log "--- properties ---"
/system/bin/getprop 2>/dev/null | grep -iE 'bluetooth|btnvram|bdroid|ro\.bt\.' \
  | redact | while IFS= read -r l; do log "prop $l"; done
log "nvram_init = $(/system/bin/getprop nvram_init)"
log "persist.mtk.wcn.combo.chipid = $(/system/bin/getprop persist.mtk.wcn.combo.chipid)"
log "ro.bt.vendor_owns_bdaddr = $(/system/bin/getprop ro.bt.vendor_owns_bdaddr)"

log "--- settings ---"
log "global bluetooth_on = $(/system/bin/settings get global bluetooth_on 2>&1)"
log "secure bluetooth_addr_valid = $(/system/bin/settings get secure bluetooth_addr_valid 2>&1)"

log "--- libraries actually present ---"
for f in /vendor/lib/libbt-vendor.so /vendor/lib/libbluetoothdrv.so \
         /vendor/lib/libbluetooth_mtk.so /lib/libbluetoothdrv.so \
         /lib/libbluetooth_mtk.so /vendor/lib/hw/android.hardware.bluetooth@1.0-impl.so; do
  if [ -e "$f" ]; then log "lib $f $(ls -l "$f" | awk '{print $5" bytes"}')"; else log "lib $f MISSING"; fi
done

log "--- binder ---"
log "$(/system/bin/service check bluetooth_manager 2>&1)"
log "--- dumpsys bluetooth_manager (head 80) ---"
/system/bin/dumpsys bluetooth_manager 2>&1 | head -80 | redact \
  | while IFS= read -r l; do log "dump $l"; done

log "--- processes and mapped vendor libraries ---"
/system/bin/ps -A 2>/dev/null | grep -iE 'bluetooth|bt_' \
  | while IFS= read -r l; do log "ps $l"; done
for pid in $(/system/bin/pidof android.hardware.bluetooth@1.0-service 2>/dev/null); do
  log "maps pid $pid"
  grep -aE 'libbt-vendor|libbluetoothdrv|libbluetooth_mtk|libbt-hci' "/proc/$pid/maps" 2>/dev/null \
    | awk '{print $6}' | sort -u | while IFS= read -r l; do log "map $l"; done
done

log "--- package state ---"
log "pm list packages -d: $(/system/bin/pm list packages -d 2>/dev/null | grep -i bluetooth)"
log "pm list packages:    $(/system/bin/pm list packages 2>/dev/null | grep -i bluetooth)"
log "pm path: $(/system/bin/pm path com.android.bluetooth 2>&1)"

log "--- logcat history (D and above, filtered) ---"
/system/bin/logcat -d -b main -b system -b crash -v threadtime 2>/dev/null \
  | grep -aE 'BluetoothManagerService|BluetoothAdapter|AdapterService|BluetoothHci|BluetoothService|bt_vendor|z1-bt-vendor|bt_stack|btif|bt_hci|Btservice' \
  | tail -150 | redact | while IFS= read -r l; do log "log $l"; done

log "--- kernel stpbt/btif ---"
/system/bin/dmesg 2>/dev/null | grep -aiE 'stpbt|BT_open|btif|stp_wmt|stp_bt|wmt' \
  | tail -40 | while IFS= read -r l; do log "kern $l"; done

log "--- nodes / selinux ---"
ls -l /dev/stpbt /dev/stpwmt 2>&1 | while IFS= read -r l; do log "dev $l"; done
log "selinux: $(/system/bin/getprop ro.boot.selinux)"

log "=== bluetooth diag end ==="

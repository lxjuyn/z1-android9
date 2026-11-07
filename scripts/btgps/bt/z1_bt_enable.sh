#!/system/bin/sh
# z1_bt_enable.sh - headless Bluetooth enable for the Z1 Android 9 image.
#
# MUST run as root (init service user root, or a root shell).  The serial
# console runs as 'shell' (uid 2000) which does NOT hold BLUETOOTH_ADMIN, so
# BluetoothManagerService.enable() rejects it; uid 0 passes
# ActivityManager.checkComponentPermission() unconditionally.
#
# What it does:
#   1. reads Settings.Global.bluetooth_on (the value BluetoothManagerService
#      uses at construction time to decide whether to auto-enable at boot);
#   2. persists it as 1;
#   3. waits for the bluetooth_manager binder (published at boot phase 500);
#   4. calls the public API via "svc bluetooth enable" (app_process as root),
#      falling back to the raw IBluetoothManager.enable(String) transaction
#      (code 6) if that fails;
#   5. dumps the resulting state.
log() { printf '<6>[z1bten] %s\n' "$*" > /dev/kmsg 2>/dev/null || true; }

case "$(/system/bin/id -u 2>/dev/null)" in
  0) ;;
  *) log "ERROR: must run as root (uid=$(/system/bin/id -u 2>/dev/null)); console shell cannot pass BLUETOOTH_ADMIN"; exit 1 ;;
esac

i=0
while [ "$(/system/bin/getprop sys.boot_completed)" != "1" ] && [ $i -lt 600 ]; do
    sleep 2; i=$((i + 2))
done
log "boot_completed after ${i}s"

cur=$(/system/bin/settings get global bluetooth_on 2>/dev/null)
log "bluetooth_on before = ${cur:-<unset>}"
case "$cur" in
  1) ;;
  *) /system/bin/settings put global bluetooth_on 1 && log "bluetooth_on set to 1 (takes effect at next boot too)" ;;
esac

i=0
while [ $i -lt 120 ]; do
    if /system/bin/service check bluetooth_manager 2>/dev/null | grep -q "Service bluetooth_manager: found"; then
        break
    fi
    sleep 1; i=$((i + 1))
done
log "$(/system/bin/service check bluetooth_manager 2>&1)"

log "--- attempt 1: svc bluetooth enable ---"
if [ -x /system/bin/svc ]; then
    /system/bin/svc bluetooth enable 2>&1 | while IFS= read -r l; do log "svc $l"; done
else
    log "svc not present"
fi

sleep 3
if /system/bin/dumpsys bluetooth_manager 2>/dev/null | grep -qE 'enabled: true|state: ON'; then
    log "svc path succeeded"
else
    log "--- attempt 2: raw IBluetoothManager.enable(String), tx code 6 ---"
    /system/bin/service call bluetooth_manager 6 s16 com.android.shell 2>&1 \
      | while IFS= read -r l; do log "call $l"; done
    sleep 3
fi

log "--- final state ---"
/system/bin/dumpsys bluetooth_manager 2>/dev/null \
  | grep -aE 'enabled:|state:|address:|name:|Bluetooth Status' \
  | while IFS= read -r l; do log "state $l"; done
log "/dev/stpbt: $(ls -l /dev/stpbt 2>&1)"
if /system/bin/dumpsys bluetooth_manager 2>/dev/null | grep -qE 'enabled: true'; then
    log "RESULT: Bluetooth is ON"
else
    log "RESULT: still OFF. bluetooth_on is now persisted as 1; reboot and"
    log "        BluetoothManagerService.handleOnBootPhase() (boot phase 550)"
    log "        will auto-enable it as system_server (uid 1000)."
fi
log "=== done ==="

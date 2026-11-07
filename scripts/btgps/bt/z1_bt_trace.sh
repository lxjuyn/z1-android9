#!/system/bin/sh
# z1_bt_trace.sh - relay Bluetooth framework/stack log lines to the serial console.
#
# Why: the normal z1_klog_relay.sh only forwards logcat W/E/F ('*:W'), but every
# decisive BluetoothManagerService log line is Slog.d (D).  Start this early
# (class core, before system_server reaches boot phase 550) and the enable
# decision becomes visible on the 921600 console.
#
# Deploy: /system/bin/z1_bt_trace.sh (0755) + z1_bt_trace.rc; see z1_bt_enable.rc.
log() { printf '<6>[z1bt] %s\n' "$*" > /dev/kmsg 2>/dev/null || true; }

# Wait for logd
i=0
while [ ! -e /dev/socket/logd ] && [ $i -lt 60 ]; do sleep 1; i=$((i+1)); done
log "bt trace start uptime=$(cut -d. -f1 /proc/uptime 2>/dev/null)s"

# Tag-filtered, line oriented.  logcat flushes per line, so the relay keeps up.
/system/bin/logcat -b main -b system -b crash -v threadtime \
    -s BluetoothManagerService:V BluetoothAdapter:V AdapterService:V \
       BluetoothHci:V BluetoothService:V bt_vendor:V bt_stack:V btif:V \
       bt_hci:V bt_snoop:V btsnoop:V 2>/dev/null \
  | while IFS= read -r L; do
        printf '<6>[z1bt] %s\n' "$L" > /dev/kmsg 2>/dev/null
    done

log "bt trace stopped"

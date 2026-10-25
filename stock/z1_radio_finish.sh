#!/system/bin/sh
# R3c: finish what /system/bin/z1_linux7_radio_init.sh cannot - it skips at
# its toolbox-era `chown 1000 1000` step (toybox wants owner:group), leaving
# the WMT/BT transport up (modules + /dev/stpwmt + /dev/stpbt created, all
# 26 DT gates passed) but the readiness property unset, so downstream
# factory radio helpers never start. This script waits for those nodes,
# fixes ownership with toybox syntax and publishes the properties.

log() { printf '<6>[z1radio] %s\n' "$*" > /dev/kmsg 2>/dev/null || true; }

# The radio init script runs from the same post-fs wave; give it time.
waited=0
while [ $waited -lt 120 ]; do
    [ -c /dev/stpwmt ] && [ -c /dev/stpbt ] && break
    sleep 2
    waited=$((waited + 2))
done
if [ ! -c /dev/stpwmt ] || [ ! -c /dev/stpbt ]; then
    log "transport nodes absent after ${waited}s; radio init did not get that far"
    exit 0
fi

/system/bin/chmod 0660 /dev/stpwmt /dev/stpbt 2>/dev/null
/system/bin/chown 1000:1000 /dev/stpwmt 2>/dev/null || true
/system/bin/chown 1002:1001 /dev/stpbt 2>/dev/null || true
log "transport nodes owned: stpwmt=$(/system/bin/stat -c '%u:%g' /dev/stpwmt 2>/dev/null) stpbt=$(/system/bin/stat -c '%u:%g' /dev/stpbt 2>/dev/null)"

if [ -c /dev/wmtWifi ]; then
    /system/bin/chmod 0660 /dev/wmtWifi 2>/dev/null || true
    /system/bin/chown 1000:1000 /dev/wmtWifi 2>/dev/null || true
    /system/bin/setprop sys.z1.radio.wifi_modules_ready 1 2>/dev/null
    log "wmtWifi node ready; wifi_modules_ready=1"
else
    /system/bin/setprop sys.z1.radio.wifi_modules_ready 0 2>/dev/null
    log "wmtWifi node absent; wifi chain masked (expected: R9 script masks wlan)"
fi

/system/bin/setprop sys.z1.radio.modules_ready 1 2>/dev/null
log "modules_ready=1 published; system-side factory radio helpers may now start"
exit 0

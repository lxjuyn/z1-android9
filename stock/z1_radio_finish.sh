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

# R3e (A8-4): turn off WMT quick-sleep before the ownership fixes and before
# the launcher ignition can put the chip through a patch download - a deep
# power state during download was the suspected stall. (EMI base address
# question stays log-only this round; waits for on-board WMT logs.)
echo '1 0' > /proc/driver/wmt_dbg 2>/dev/null || true

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

# R3e (A1-3): persist the WiFi NVRAM calibration (and the regdb, when packed)
# into /system/etc/firmware so the WLAN stack reads them from flash instead of
# the ramdisk. Idempotent: skipped whenever the target already exists. One
# remount,rw window for both copies; remount,ro runs even if a copy fails.
need_nvram=0
need_regdb=0
[ -s /system/etc/firmware/nvram/WIFI ] || need_nvram=1
[ -s /system/etc/firmware/regulatory.db ] || need_regdb=1
if [ "$need_nvram" -eq 1 ] || [ "$need_regdb" -eq 1 ]; then
    if mount -o remount,rw /system 2>/dev/null; then
        if [ "$need_nvram" -eq 1 ]; then
            mkdir -p /system/etc/firmware/nvram
            cp /wifi-nvram-WIFI.bin /system/etc/firmware/nvram/WIFI
            log "NVRAM persisted: $(wc -c < /system/etc/firmware/nvram/WIFI 2>/dev/null) bytes"
        fi
        if [ "$need_regdb" -eq 1 ]; then
            cp /regulatory.db /system/etc/firmware/regulatory.db
            log "regulatory.db persisted: $(wc -c < /system/etc/firmware/regulatory.db 2>/dev/null) bytes"
        fi
        sync
        mount -o remount,ro /system 2>/dev/null
    else
        log "remount,rw /system failed; NVRAM/regdb not persisted this boot"
    fi
fi
exit 0

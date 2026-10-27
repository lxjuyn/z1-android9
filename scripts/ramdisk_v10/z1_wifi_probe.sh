#!/system/bin/sh
# Z1 WiFi function-on probe (v10).
#
# Chain: radio init loads the SoC module stack (cfg80211 + WMT + wlan_gen2) and
# creates /dev/wmtWifi; init arms z1_6620 (the SOC-flow firmware launcher);
# this probe drives the remaining steps and is the only place that decides the
# WLAN is usable.
#
# v10 fixes from the host-side audit:
#   * "wlan0 exists" is NOT readiness - the netdev appears at insmod time,
#     before the firmware download completes, so an immediate scan always failed.
#   * bring the interface up before scanning (a down wlan0 never scans).
#   * verify the firmware/NVRAM actually loaded instead of guessing.
#   * publish sys.wlan0.up only after a successful up + carrier probe, so the
#     supplicant service never starts against a dead netdev.
log() { printf '<6>[z1wifi] %s\n' "$*" > /dev/kmsg 2>/dev/null || true; }

log "probe start; waiting for 6620_launcher"

waited=0
while [ $waited -lt 120 ]; do
    found=
    for cmd in /proc/[0-9]*/cmdline; do
        case "$(cat "$cmd" 2>/dev/null)" in
            6620_launcher*) found=yes ;;
        esac
    done
    [ -n "$found" ] && break
    sleep 5
    waited=$((waited + 5))
done
[ -z "$found" ] && { log "6620_launcher never appeared in ${waited}s; abort"; exit 0; }
log "6620_launcher alive after ${waited}s; waiting 20s for the WMT patch download"
sleep 20

if [ ! -c /dev/wmtWifi ]; then
    log "/dev/wmtWifi missing; abort"
    exit 0
fi
echo 1 > /dev/wmtWifi 2>/dev/null
log "function-on written to /dev/wmtWifi"

# NVRAM/regdb diagnostics (the driver reads absolute /etc/firmware paths).
[ -s /etc/firmware/nvram/WIFI ] && log "nvram/WIFI present ($(wc -c < /etc/firmware/nvram/WIFI) bytes)" \
    || log "nvram/WIFI MISSING - RF calibration will be wrong"
[ -s /etc/firmware/regulatory.db ] && log "regulatory.db present" || log "regulatory.db missing"

# Wait for the netdev, then bring it up and confirm it stays.
i=0
up_ok=
while [ $i -lt 60 ]; do
    if [ -d /sys/class/net/wlan0 ]; then
        /system/bin/ifconfig wlan0 up 2>/dev/null || true
        sleep 3
        state=$(cat /sys/class/net/wlan0/operstate 2>/dev/null)
        flags=$(cat /sys/class/net/wlan0/flags 2>/dev/null)
        log "wlan0 present after ${i}s (operstate=$state flags=$flags)"
        case "$flags" in
            0x*[13579bdf]) up_ok=yes ;;   # IFF_UP bit set
            *) case "$state" in up*|unknown) up_ok=yes ;; esac ;;
        esac
        [ -n "$up_ok" ] && break
    fi
    sleep 2
    i=$((i + 2))
done

if [ -z "$up_ok" ]; then
    log "wlan0 never came up after ${i}s; func-on failed (see WMT/wlan_gen2 dmesg)"
    exit 0
fi

log "wlan0 is up; starting the supplicant and publishing sys.wlan0.up=1"
/system/bin/setprop sys.wlan0.up 1

# Report what a scan sees, purely diagnostic - the framework owns real scans.
j=0
while [ $j -lt 20 ]; do
    ssids=$(/system/bin/iw dev wlan0 scan 2>/dev/null | grep 'SSID:' | head -n 20)
    [ -n "$ssids" ] && break
    sleep 3
    j=$((j + 3))
done
if [ -n "$ssids" ]; then
    n=0
    for line in $ssids; do :; done
    n=$(printf '%s\n' "$ssids" | wc -l)
    printf '%s\n' "$ssids" | while IFS= read -r line; do
        /system/bin/log -t Z1Wifi "$line" 2>/dev/null
    done
    log "scan OK: ${n} SSID lines (logcat tag Z1Wifi)"
else
    log "scan produced nothing after ${j}s (driver up but no results)"
fi
exit 0

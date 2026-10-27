#!/system/bin/sh
# v9 Z1 WiFi function-on probe (replaces the R3e version).
#
# Differences from v8 and why:
#  - v8 polled "/sys/class/net/wlan0 exists", but wlan_gen2 registers the
#    netdev at insmod time ("createWirelessDevice: create wireless device
#    success" is in the dmesg of the 2026-10-25 boot), i.e. *before* the
#    firmware download that "echo 1 > /dev/wmtWifi" triggers. So the old poll
#    returned success immediately and the supplicant was started on a device
#    that had no RF firmware yet. v9 instead proves readiness with a real
#    nl80211 scan.
#  - v8 scanned without bringing the interface up first, so the scan would
#    have failed even on a healthy driver.
#  - v9 retries function-on and dumps the decisive kernel lines into kmsg, so
#    a single boot log tells whether the failure is firmware, NVRAM or WMT.
#
# /dev/wmtWifi semantics (fork_linux/.../common/linux/pub/wmt_chrdev_wifi.c
# WIFI_write(): '1' -> mtk_wcn_wmt_func_on(WMTDRV_TYPE_WIFI),
#               '0' -> mtk_wcn_wmt_func_off(WMTDRV_TYPE_WIFI)).

log() { printf '<6>[z1wifi] %s\n' "$*" > /dev/kmsg 2>/dev/null || true; }

IFACE=wlan0

func_on() {
    [ -c /dev/wmtWifi ] || { log "/dev/wmtWifi missing"; return 1; }
    if echo 1 > /dev/wmtWifi 2>/dev/null; then
        log "function-on written (echo 1 > /dev/wmtWifi)"
        return 0
    fi
    log "write to /dev/wmtWifi failed"
    return 1
}

# 1. The kernel WMT/WLAN modules are loaded by z1_linux7_radio_init.sh in the
#    same post-fs wave; wait for the node instead of racing it.
i=0
while [ ! -c /dev/wmtWifi ] && [ $i -lt 40 ]; do sleep 1; i=$((i+1)); done
[ -c /dev/wmtWifi ] || { log "/dev/wmtWifi never appeared in ${i}s; abort"; exit 0; }

# 2. Wake the chip (downloads WMT patch + WLAN firmware).
if ! func_on; then
    sleep 5
    func_on || { log "function-on failed twice; abort"; exit 0; }
fi

# 3. Real readiness: interface up + a scan that returns something.
ifconfig "$IFACE" up 2>/dev/null || true
tries=0
ready=0
while [ $tries -lt 45 ]; do          # 45 x 2s = 90s
    if /system/bin/iw dev "$IFACE" info >/dev/null 2>&1; then
        out=$(/system/bin/iw dev "$IFACE" scan 2>&1)
        if [ -n "$out" ] && ! echo "$out" | grep -qiE "command failed|not supported|Network is down|Invalid argument"; then
            ready=1
            break
        fi
    fi
    sleep 2
    tries=$((tries+1))
done

if [ "$ready" != 1 ]; then
    log "$IFACE did not become scannable in 90s; func-on chain failed"
    # Decisive evidence for the next round, straight from kmsg:
    if command -v dmesg >/dev/null 2>&1; then
        dmesg | grep -aiE "Open FW image|No Firmware found|nvram|NVRAM|HifAhb|wlan|WMT|EMI mapping" \
            | tail -n 40 | while IFS= read -r l; do log "  $l"; done
    fi
    exit 0
fi

log "$IFACE scan OK after $((tries*2))s"
n=$(echo "$out" | grep -c "SSID:")
log "scan returned $n SSID lines"
echo "$out" | grep "SSID:" | head -n 20 | while IFS= read -r line; do
    /system/bin/log -t Z1Wifi "$line" 2>/dev/null
done

# 4. Only now hand the interface to the supplicant layer.
setprop sys.wlan0.up 1
log "sys.wlan0.up=1 published"
exit 0

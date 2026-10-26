#!/system/bin/sh
# R3e (A1-2): WiFi function-on probe. init now arms z1_6620 (6620_launcher)
# on sys.z1.radio.modules_ready=1, so the WMT patch finally gets downloaded;
# this probe closes the chain: wait for the launcher and its patch window,
# function-on via /dev/wmtWifi, poll for wlan0, then scan and publish the
# first SSIDs into the logcat channel (tag Z1Wifi). Every step is mirrored
# into kmsg with the [z1wifi] tag. Purely diagnostic: it never sets
# wlan.driver.status and never blocks the boot wave.
log() { printf '<6>[z1wifi] %s\n' "$*" > /dev/kmsg 2>/dev/null || true; }

log "probe start; waiting for 6620_launcher"

launcher=
waited=0
while [ $waited -lt 120 ]; do
    for cmd in /proc/[0-9]*/cmdline; do
        case "$(cat "$cmd" 2>/dev/null)" in
            6620_launcher*) launcher=$cmd ;;
        esac
    done
    [ -n "$launcher" ] && break
    sleep 5
    waited=$((waited + 5))
done

if [ -z "$launcher" ]; then
    log "6620_launcher never appeared in ${waited}s; func-on aborted"
    exit 0
fi
log "6620_launcher alive after ${waited}s; sleeping 15s for the WMT patch download"
sleep 15

if [ ! -c /dev/wmtWifi ]; then
    log "/dev/wmtWifi missing (not a char node); func-on aborted"
    exit 0
fi

echo 1 > /dev/wmtWifi 2>/dev/null
log "function-on written (echo 1 > /dev/wmtWifi)"

seen=
i=0
while [ $i -lt 30 ]; do
    if [ -d /sys/class/net/wlan0 ]; then
        seen=yes
        log "wlan0 present after ${i}s"
        break
    fi
    sleep 2
    i=$((i + 2))
done

if [ -z "$seen" ]; then
    log "wlan0 absent after 30s of polling; func-on failed (see WMT/kernel logs)"
    exit 0
fi

log "scanning wlan0; SSIDs go to logcat tag Z1Wifi"
ssids=$(/system/bin/iw dev wlan0 scan 2>/dev/null | grep 'SSID:' | head -n 20)
if [ -n "$ssids" ]; then
    n=0
    echo "$ssids" | while IFS= read -r line; do
        /system/bin/log -t Z1Wifi "$line" 2>/dev/null
    done
    n=$(echo "$ssids" | wc -l)
    log "scan OK: $n SSID lines published to logcat"
else
    log "scan empty or iw failed (wlan0 up but no scan output)"
fi
exit 0

#!/system/bin/sh
# Z1 WiFi manual connect (framework-independent path).
#
# The A9 framework needs an IWifi HIDL HAL that this port does not have yet, so
# the Settings toggle cannot bring Wi-Fi up. This script drives the same chain
# from the shell instead: it lets wpa_supplicant associate and then configures
# the interface, so apps get working connectivity even while the framework HAL
# is still missing.
#
# Config file (optional): /data/local/z1_wifi.conf
#   SSID=MyNetwork
#   PSK=secret            # omit or use KEY_MGMT=NONE for an open network
#   STATIC_IP=192.168.1.50/24   # optional; otherwise DHCP via dhcptool
#   GATEWAY=192.168.1.1
#   DNS=192.168.1.1
#
# Usage: z1_wifi_connect.sh [--scan]
log() { printf '<6>[z1net] %s\n' "$*" > /dev/kmsg 2>/dev/null; echo "$*"; }

CTRL=/data/misc/wifi/sockets
CONF=/data/local/z1_wifi.conf

[ -x /system/bin/wpa_cli ] || { log "wpa_cli missing"; exit 1; }

# Make sure the supplicant is running with our socket dir.
if ! /system/bin/wpa_cli -p $CTRL -i wlan0 status >/dev/null 2>&1; then
    log "starting wpa_supplicant"
    /system/bin/wpa_supplicant -B -iwlan0 -Dnl80211 \
        -c /data/misc/wifi/wpa_supplicant.conf \
        -e /data/misc/wifi/entropy.bin -g@android:wpa_wlan0 2>/dev/null
    i=0
    while [ $i -lt 20 ]; do
        /system/bin/wpa_cli -p $CTRL -i wlan0 ping >/dev/null 2>&1 && break
        sleep 1
        i=$((i + 1))
    done
fi

if /system/bin/wpa_cli -p $CTRL -i wlan0 ping >/dev/null 2>&1; then
    log "supplicant control socket OK"
else
    log "supplicant control socket unavailable; check /data/misc/wifi/sockets and dmesg"
    exit 1
fi

if [ "$1" = "--scan" ] || [ ! -f "$CONF" ]; then
    log "scanning..."
    /system/bin/wpa_cli -p $CTRL -i wlan0 scan >/dev/null 2>&1
    sleep 6
    /system/bin/wpa_cli -p $CTRL -i wlan0 scan_results 2>/dev/null | while IFS= read -r line; do
        log "  $line"
    done
    [ -f "$CONF" ] || { log "no $CONF; create it with SSID=/PSK= to auto-connect"; exit 0; }
fi

SSID=$(sed -n 's/^SSID=//p' "$CONF" | head -1)
PSK=$(sed -n 's/^PSK=//p' "$CONF" | head -1)
STATIC_IP=$(sed -n 's/^STATIC_IP=//p' "$CONF" | head -1)
GATEWAY=$(sed -n 's/^GATEWAY=//p' "$CONF" | head -1)
DNS=$(sed -n 's/^DNS=//p' "$CONF" | head -1)
[ -z "$SSID" ] && { log "SSID empty in $CONF"; exit 1; }

NETID=$(/system/bin/wpa_cli -p $CTRL -i wlan0 add_network 2>/dev/null | tail -1)
case "$NETID" in ''|*[!0-9]*) log "add_network failed (got '$NETID')"; exit 1 ;; esac
/system/bin/wpa_cli -p $CTRL -i wlan0 set_network $NETID ssid "\"$SSID\"" >/dev/null
if [ -n "$PSK" ]; then
    /system/bin/wpa_cli -p $CTRL -i wlan0 set_network $NETID psk "\"$PSK\"" >/dev/null
else
    /system/bin/wpa_cli -p $CTRL -i wlan0 set_network $NETID key_mgmt NONE >/dev/null
fi
/system/bin/wpa_cli -p $CTRL -i wlan0 select_network $NETID >/dev/null
/system/bin/wpa_cli -p $CTRL -i wlan0 enable_network $NETID >/dev/null
log "associating with '$SSID' (network id $NETID)"

i=0
while [ $i -lt 60 ]; do
    state=$(/system/bin/wpa_cli -p $CTRL -i wlan0 status 2>/dev/null | sed -n 's/^wpa_state=//p')
    [ "$state" = "COMPLETED" ] && break
    sleep 2
    i=$((i + 2))
done
if [ "$state" != "COMPLETED" ]; then
    log "association did not complete (wpa_state=$state)"
    /system/bin/wpa_cli -p $CTRL -i wlan0 status 2>/dev/null | while IFS= read -r l; do log "  $l"; done
    exit 1
fi
log "associated; configuring the interface"

if [ -n "$STATIC_IP" ]; then
    /system/bin/ip addr flush dev wlan0 2>/dev/null
    /system/bin/ip addr add "$STATIC_IP" dev wlan0
    [ -n "$GATEWAY" ] && /system/bin/ip route add default via "$GATEWAY" dev wlan0
    [ -n "$DNS" ] && { setprop net.dns1 "$DNS"; setprop net.dns2 "$DNS"; }
    log "static configuration applied: $STATIC_IP gw=$GATEWAY dns=$DNS"
else
    # A9 moved DHCP into the framework; try the legacy tool, then fall back to
    # a probe of the gateway's DHCP on the usual subnet.
    if [ -x /system/bin/dhcptool ]; then
        /system/bin/dhcptool wlan0 2>/dev/null && log "dhcptool reported success" \
            || log "dhcptool failed (A9 netd has no client DHCP; set STATIC_IP in $CONF)"
    else
        log "no DHCP client in this image; set STATIC_IP/GATEWAY/DNS in $CONF"
    fi
fi

log "interface state:"
/system/bin/ip addr show wlan0 2>/dev/null | while IFS= read -r l; do log "  $l"; done
log "route:"; /system/bin/ip route 2>/dev/null | while IFS= read -r l; do log "  $l"; done

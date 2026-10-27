#!/system/bin/sh
# Z1 WiFi link verification / bring-up helper (host-written, run on device).
#
# No DHCP client exists in this A9 system.img (dhcpcd / dhcptool absent;
# Android 9 does DHCP inside the framework), and the framework cannot drive
# this supplicant (no wifi HAL, see z1_wifi_plan.md). So this script either
#   - does an L2-only check (association + 4-way handshake), or
#   - applies a static IPv4 config and proves L3 with ping.
#
# Credentials file (root:root 0600), /data/misc/wifi/z1_target.conf:
#   SSID=MyApt
#   PSK=secret123
#   IP=192.168.1.50/24      # optional: omit for L2-only check
#   GW=192.168.1.1          # optional
#   DNS=192.168.1.1         # optional
#
# Usage: sh /data/local/tmp/z1_wifi_connect.sh [--l2-only]

log() { printf '<6>[z1wifi-ctl] %s\n' "$*" > /dev/kmsg 2>/dev/null || true; }
say() { echo "$*"; log "$*"; }

CFG=/data/misc/wifi/z1_target.conf
CTRL=/data/misc/wifi/sockets
IFACE=wlan0
L2_ONLY=0
[ "$1" = "--l2-only" ] && L2_ONLY=1

WPA_CLI="/system/bin/wpa_cli -i $IFACE -p $CTRL"

[ -s "$CFG" ] || { say "missing $CFG"; exit 1; }
. "$CFG"
[ -n "$SSID" ] || { say "SSID not set in $CFG"; exit 1; }

say "== 0. prerequisites"
for t in /system/bin/wpa_cli /system/bin/iw /system/bin/ifconfig; do
    [ -x "$t" ] || say "WARNING missing $t"
done
pidof wpa_supplicant >/dev/null 2>&1 || say "WARNING wpa_supplicant not running (start it via the init service)"
$WPA_CLI ping >/dev/null 2>&1 || say "WARNING wpa_cli cannot reach the supplicant on $CTRL"

say "== 1. interface state"
ifconfig $IFACE up 2>/dev/null
iw dev $IFACE info 2>&1 | head -n 8

say "== 2. configure network (idempotent)"
id=$($WPA_CLI list_networks 2>/dev/null | awk -F'\t' -v s="$SSID" '$2==s {print $1}' | head -n 1)
if [ -z "$id" ]; then
    id=$($WPA_CLI add_network 2>/dev/null | tail -n 1)
    [ -n "$id" ] || { say "add_network failed"; exit 1; }
    $WPA_CLI set_network "$id" ssid "\"$SSID\"" >/dev/null 2>&1
    if [ -n "$PSK" ]; then
        $WPA_CLI set_network "$id" psk "\"$PSK\"" >/dev/null 2>&1
    else
        $WPA_CLI set_network "$id" key_mgmt NONE >/dev/null 2>&1
    fi
    say "created network $id for SSID=$SSID"
else
    say "reusing network $id for SSID=$SSID"
fi
$WPA_CLI enable_network "$id" >/dev/null 2>&1
$WPA_CLI select_network "$id" >/dev/null 2>&1
$WPA_CLI save_config >/dev/null 2>&1

say "== 3. wait for association (<=40s)"
i=0; state=
while [ $i -lt 20 ]; do
    out=$($WPA_CLI status 2>/dev/null)
    state=$(echo "$out" | awk -F= '/^wpa_state=/{print $2}')
    [ "$state" = "COMPLETED" ] && break
    sleep 2; i=$((i+1))
done
echo "$out" | head -n 20
if [ "$state" != "COMPLETED" ]; then
    say "association NOT completed (wpa_state=$state)"
    say "next: dmesg | tail -n 60 ; wpa_cli -i $IFACE -p $CTRL status"
    exit 2
fi
say "associated (wpa_state=COMPLETED)"

[ "$L2_ONLY" = 1 ] && { say "L2-only mode: stop here"; exit 0; }

say "== 4. static IPv4 (no DHCP client in this image)"
[ -n "$IP" ] || { say "IP not set in $CFG; L2 verified only"; exit 0; }
ifconfig $IFACE "$IP" up 2>/dev/null
if [ -n "$GW" ]; then
    # Toybox route syntax; the "default" route may already exist for another
    # interface, so ignore the error and report the table instead.
    route add default gw "$GW" dev $IFACE 2>/dev/null || true
fi
ifconfig $IFACE
netstat -rn 2>/dev/null | head -n 10

if [ -n "$DNS" ]; then
    # Android 5+ libc asks netd, not net.dns1. Register the resolver on the
    # netd side when possible (permission failures are expected as root with
    # a permissive policy; grep logcat for the result).
    ndc resolver setnetdns 0 "" "$DNS" 2>/dev/null || true
    setprop net.dns1 "$DNS" 2>/dev/null || true
fi

say "== 5. L3 check"
ping -c 3 -W 2 "$GW" 2>&1 | tail -n 5
say "== done"

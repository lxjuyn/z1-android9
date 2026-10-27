#!/system/bin/sh
# v9 Z1 WiFi supplicant starter (fixes two hard failures of the v8 version).
#
# v8 bugs fixed here:
#  1. Path: the v8 init service ran "/system/bin/z1_wpa_start.sh", but the
#     script is packed at the *ramdisk root* (/z1_wpa_start.sh) and
#     /system/bin/z1_wpa_start.sh does not exist in system.img. The service
#     could never exec. v9 is referenced as /z1_wpa_start.sh, and also
#     re-execs itself from whichever copy exists.
#  2. Config: /system/etc/wifi/wpa_supplicant.conf ships
#     "ctrl_interface=wlan0", which makes wpa_supplicant create a directory
#     named "wlan0" in the CWD instead of a control socket directory, so
#     wpa_cli could never talk to it. v9 rewrites the directive to
#     /data/misc/wifi/sockets on every start (idempotent).
#
# Not fixed here (needs an A9 wifi HAL, see z1_wifi_plan.md §8/§10): the
# Android 9 framework reaches wpa_supplicant only through
# android.hardware.wifi.supplicant@1.x HIDL. This LOS14 (2.6) supplicant
# exposes only the legacy ctrl_interface socket, so the framework will not
# drive it — wpa_cli / z1_wifi_connect.sh will.

log() { printf '<6>[z1wpa] %s\n' "$*" > /dev/kmsg 2>/dev/null || true; }

CONF_DIR=/data/misc/wifi
SOCK_DIR=$CONF_DIR/sockets
CONF=$CONF_DIR/wpa_supplicant.conf
IFACE=wlan0

i=0
while [ ! -d "/sys/class/net/$IFACE" ] && [ $i -lt 60 ]; do sleep 2; i=$((i+2)); done
[ -d "/sys/class/net/$IFACE" ] || { log "$IFACE never appeared; supplicant not started"; exit 0; }

mkdir -p "$CONF_DIR" "$SOCK_DIR" 2>/dev/null
chown 1010:1010 "$CONF_DIR" "$SOCK_DIR" 2>/dev/null
chmod 0770 "$CONF_DIR" "$SOCK_DIR" 2>/dev/null

if [ ! -s "$CONF" ]; then
    if [ -s /system/etc/wifi/wpa_supplicant.conf ]; then
        cp /system/etc/wifi/wpa_supplicant.conf "$CONF"
        log "seeded wpa_supplicant.conf from /system/etc/wifi"
    else
        printf 'update_config=1\ndevice_name=Z1\nmanufacturer=Jiabo\nmodel_name=Z1\np2p_no_group_iface=1\n' > "$CONF"
        log "wrote minimal wpa_supplicant.conf"
    fi
fi

# Force a usable control socket directory (drops the factory
# "ctrl_interface=wlan0" line, keeps every other directive).
if grep -q '^ctrl_interface=' "$CONF" 2>/dev/null; then
    grep -v '^ctrl_interface=' "$CONF" > "$CONF.tmp" &&
        printf 'ctrl_interface=%s\n' "$SOCK_DIR" >> "$CONF.tmp" &&
        mv "$CONF.tmp" "$CONF"
else
    printf 'ctrl_interface=%s\n' "$SOCK_DIR" >> "$CONF"
fi
chown 1010:1010 "$CONF" 2>/dev/null
chmod 0660 "$CONF" 2>/dev/null

touch "$CONF_DIR/entropy.bin" 2>/dev/null
chown 1010:1010 "$CONF_DIR/entropy.bin" 2>/dev/null
chmod 0600 "$CONF_DIR/entropy.bin" 2>/dev/null

log "starting wpa_supplicant on $IFACE (-Dnl80211, ctrl=$SOCK_DIR)"
exec /system/bin/wpa_supplicant -i"$IFACE" -Dnl80211 \
     -c "$CONF" \
     -e "$CONF_DIR/entropy.bin" \
     -g@android:wpa_wlan0

#!/system/bin/sh
# Z1 WiFi supplicant starter: make sure the config dirs exist, seed the config
# from factory defaults, then exec wpa_supplicant on wlan0 (nl80211).
# Called by init service z1_wpa_supplicant (see init.z1.network.rc).

log() { printf '<6>[z1wpa] %s\n' "$*" > /dev/kmsg 2>/dev/null || true; }

# Wait briefly for wlan0 to appear (the 6620 launcher downloads firmware first).
i=0
while [ ! -d /sys/class/net/wlan0 ] && [ $i -lt 60 ]; do
    sleep 2
    i=$((i + 2))
done
if [ ! -d /sys/class/net/wlan0 ]; then
    log "wlan0 never appeared; supplicant not started"
    exit 0
fi
log "wlan0 present after ${i}s"

mkdir -p /data/misc/wifi /data/misc/wifi/sockets 2>/dev/null
chown 1010:1010 /data/misc/wifi /data/misc/wifi/sockets 2>/dev/null
chmod 0770 /data/misc/wifi /data/misc/wifi/sockets 2>/dev/null

if [ ! -s /data/misc/wifi/wpa_supplicant.conf ]; then
    if [ -s /system/etc/wifi/wpa_supplicant.conf ]; then
        cp /system/etc/wifi/wpa_supplicant.conf /data/misc/wifi/wpa_supplicant.conf
        log "seeded wpa_supplicant.conf from /system/etc/wifi"
    else
        printf 'ctrl_interface=/data/misc/wifi/sockets\ndriver_param=use_p2p_group_interface=1\nupdate_config=1\ndevice_name=Z1\nmanufacturer=Jiabo\nmodel_name=Z1\n' \
            > /data/misc/wifi/wpa_supplicant.conf
        log "wrote minimal wpa_supplicant.conf"
    fi
    chown 1010:1010 /data/misc/wifi/wpa_supplicant.conf 2>/dev/null
    chmod 0660 /data/misc/wifi/wpa_supplicant.conf 2>/dev/null
fi

# Keep the entropy file writable by the supplicant user.
touch /data/misc/wifi/entropy.bin 2>/dev/null
chown 1010:1010 /data/misc/wifi/entropy.bin 2>/dev/null

log "starting wpa_supplicant on wlan0"
exec /system/bin/wpa_supplicant -iwlan0 -Dnl80211 \
     -c /data/misc/wifi/wpa_supplicant.conf \
     -e /data/misc/wifi/entropy.bin \
     -g@android:wpa_wlan0

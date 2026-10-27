#!/system/bin/sh
# Z1 GPS preparation: create the runtime dirs mnld expects, seed GPS_CHIP.cfg
# on first boot, then start the mnld service when /dev/stpgps is present.
log() { printf '<6>[z1gps] %s\n' "$*" > /dev/kmsg 2>/dev/null || true; }

mkdir -p /data/misc 2>/dev/null
chown system:system /data/misc 2>/dev/null
chmod 0771 /data/misc 2>/dev/null

if [ ! -s /data/misc/GPS_CHIP.cfg ]; then
    printf 'MTK_GPS_CHIP=MT6620\n' > /data/misc/GPS_CHIP.cfg 2>/dev/null
    chown system:system /data/misc/GPS_CHIP.cfg 2>/dev/null
    chmod 0644 /data/misc/GPS_CHIP.cfg 2>/dev/null
    log "seeded /data/misc/GPS_CHIP.cfg"
fi

# Wait for the GPS transport node (created by the kernel GPS driver, if any).
i=0
while [ ! -c /dev/stpgps ] && [ $i -lt 40 ]; do
    sleep 2
    i=$((i + 2))
done

if [ -c /dev/stpgps ]; then
    chmod 0660 /dev/stpgps 2>/dev/null
    chown 1000:1000 /dev/stpgps 2>/dev/null
    log "/dev/stpgps ready after ${i}s; starting mnld"
    start z1_mnld
else
    log "/dev/stpgps absent after ${i}s; GPS transport driver missing (mnld not started)"
fi

#!/system/bin/sh
# z1_gpsdrv_load.sh - load the gpsdrv sysfs shim before mnld starts.
# Runs as root (called from z1_gps_prep.sh or manually).
log() { printf '<6>[z1gps] %s\n' "$*" > /dev/kmsg 2>/dev/null || true; }

MODDIR=/system/lib/modules/z1-radio
KO=$MODDIR/z1_gpsdrv.ko

if [ -d /sys/class/gpsdrv/gps ]; then
    log "gpsdrv sysfs already present: $(ls /sys/class/gpsdrv/gps 2>/dev/null | tr '\n' ' ')"
    exit 0
fi

if [ ! -f "$KO" ]; then
    log "MISSING $KO - mnld will keep exiting with status 1"
    exit 1
fi

if insmod "$KO" 2>/dev/null; then
    log "insmod OK z1_gpsdrv.ko"
else
    gerr=$(insmod "$KO" 2>&1)
    log "insmod FAIL z1_gpsdrv.ko: $gerr"
    exit 1
fi

n=0
for a in status state pwrctl suspend pwrsave; do
    if [ -w "/sys/class/gpsdrv/gps/$a" ]; then
        n=$((n + 1))
    else
        log "WARN /sys/class/gpsdrv/gps/$a missing or not writable"
    fi
done
log "gpsdrv nodes ready: $n/5 (mode $(ls -l /sys/class/gpsdrv/gps/status 2>/dev/null | cut -d' ' -f1))"
[ "$n" = 5 ]

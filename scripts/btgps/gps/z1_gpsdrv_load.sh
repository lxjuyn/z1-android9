#!/system/bin/sh
# Load only the sysfs compatibility shim; this does not implement GPS power.
log() { printf '<6>[z1gps] %s\n' "$*" > /dev/kmsg 2>/dev/null || true; }
die() { log "ERROR: $*"; exit 1; }
KO=/system/lib/modules/z1-radio/z1_gpsdrv.ko
if [ ! -d /sys/class/gpsdrv/gps ]; then
    [ -f "$KO" ] || die "missing gpsdrv module"
    # Capture the error from one attempt, never call insmod twice for logging.
    err=$(insmod "$KO" 2>&1) || die "gpsdrv insmod failed: $err"
fi
for attr in status state pwrctl suspend pwrsave; do
    [ -r "/sys/class/gpsdrv/gps/$attr" ] && [ -w "/sys/class/gpsdrv/gps/$attr" ] \
        || die "gpsdrv attribute missing or inaccessible: $attr"
done
log "gpsdrv 5/5 attributes accessible to caller; daemon access and GPS operation unverified"
exit 0

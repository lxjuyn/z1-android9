#!/system/bin/sh
# install_gps.sh - on-device installer for the Z1 GPS userspace chain.
#
# Run as root.  Expects this directory layout (push the whole btgps tree to
# /data/local/tmp/btgps):
#
#   /data/local/tmp/btgps/gps/z1_gpsdrv/z1_gpsdrv.ko
#   /data/local/tmp/btgps/gps/android.hardware.location.gps.xml
#   /data/local/tmp/btgps/gps/routeB/mnld_patched        (route B only)
#
# Usage:  sh install_gps.sh [A|B]        (default A = kernel sysfs shim)
#
# Route A (recommended): load z1_gpsdrv.ko, leave /vendor/bin/mnld untouched.
# Route B            : leave the kernel alone, replace mnld with the patched
#                      binary that treats gpsdrv open() failures as success.
log() { printf '<6>[z1gpsinst] %s\n' "$*" > /dev/kmsg 2>/dev/null || true; }
die() { log "FATAL: $*"; exit 1; }

[ "$(id -u)" = 0 ] || die "must run as root"
ROUTE=${1:-A}
BASE=$(cd "$(dirname "$0")" && pwd)
MODDIR=/system/lib/modules/z1-radio

log "route=$ROUTE base=$BASE"

mount -o remount,rw / 2>/dev/null || log "remount / rw failed (system may be read-only)"

# --- common: GPS feature permission --------------------------------------
if [ -f "$BASE/android.hardware.location.gps.xml" ]; then
    cp "$BASE/android.hardware.location.gps.xml" /system/etc/permissions/ \
        && chmod 0644 /system/etc/permissions/android.hardware.location.gps.xml \
        && log "installed android.hardware.location.gps.xml"
else
    log "WARN android.hardware.location.gps.xml not found; framework will not expose GPS"
fi

# --- route specific -------------------------------------------------------
case "$ROUTE" in
  A|a)
    [ -f "$BASE/z1_gpsdrv/z1_gpsdrv.ko" ] || die "missing z1_gpsdrv.ko"
    mkdir -p "$MODDIR" 2>/dev/null
    cp "$BASE/z1_gpsdrv/z1_gpsdrv.ko" "$MODDIR/z1_gpsdrv.ko" \
        && chmod 0644 "$MODDIR/z1_gpsdrv.ko" \
        || die "could not install z1_gpsdrv.ko"
    log "installed $MODDIR/z1_gpsdrv.ko"
    if [ -d /sys/class/gpsdrv/gps ]; then
        log "gpsdrv already loaded"
    else
        insmod "$MODDIR/z1_gpsdrv.ko" || die "insmod z1_gpsdrv.ko failed"
        log "insmod OK"
    fi
    ;;
  B|b)
    [ -f "$BASE/routeB/mnld_patched" ] || die "missing routeB/mnld_patched"
    [ -f /vendor/bin/mnld.orig ] || cp /vendor/bin/mnld /vendor/bin/mnld.orig
    cp "$BASE/routeB/mnld_patched" /vendor/bin/mnld \
        && chmod 0755 /vendor/bin/mnld \
        || die "could not install patched mnld"
    log "installed patched mnld (backup: /vendor/bin/mnld.orig)"
    ;;
  *) die "unknown route $ROUTE (use A or B)" ;;
esac

# --- verify the gpsdrv contract ------------------------------------------
n=0
for a in status state pwrctl suspend pwrsave; do
    [ -w "/sys/class/gpsdrv/gps/$a" ] && n=$((n + 1))
done
log "gpsdrv writable nodes: $n/5"
[ "$n" = 5 ] || log "WARN: gpsdrv nodes incomplete; mnld may still fail"

# --- restart mnld ---------------------------------------------------------
# Runtime stopgap for the service-name mismatch: gps.default.so starts
# "mnld" and waits for init.svc.mnld.  The ramdisk currently names the service
# z1_mnld; after the rc rename (init.z1.network.rc.patch) this setprop is
# unnecessary.  In permissive mode init.svc.* is writable by root.
setprop init.svc.mnld running 2>/dev/null || log "WARN could not set init.svc.mnld"
stop z1_mnld 2>/dev/null
sleep 1
start z1_mnld
sleep 5

if ps -A 2>/dev/null | grep -q '[m]nld'; then
    log "mnld is running: $(ps -A 2>/dev/null | grep '[m]nld' | head -1)"
else
    log "mnld is NOT running - see logcat for the next failure after gpsdrv"
fi

log "done. For the full picture run: sh /system/bin/z1_bt_diag.sh  (bluetooth)"
log "GPS logcat: logcat -d -s gps.default mnld MNLD"

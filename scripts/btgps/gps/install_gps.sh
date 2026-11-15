#!/system/bin/sh
# Install the gpsdrv compatibility shim and request GPS preparation.
# Usage: sh install_gps.sh [A]. This is not a complete GPS implementation.
log() { printf '<6>[z1gpsinst] %s\n' "$*" > /dev/kmsg 2>/dev/null || true; }
die() { log "ERROR: $*"; exit 1; }
SYSTEM_RW=0
restore_system_ro() {
    status=$?
    trap - 0
    if [ "$SYSTEM_RW" = 1 ]; then
        if ! mount -o remount,ro /system; then
            log "ERROR: cannot restore /system read-only"
            [ "$status" -ne 0 ] || status=1
        fi
    fi
    exit "$status"
}
trap 'restore_system_ro' 0
trap 'exit 1' HUP INT TERM
ROUTE=${1:-A}
case "$ROUTE" in
    A|a) ;;
    B|b) die "Route B disabled: hiding gpsdrv failures does not repair GPS" ;;
    *) die "unknown route (only A is supported)" ;;
esac
[ "$(id -u)" = 0 ] || die "must run as root"
BASE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || die "cannot locate installation files"
MODDIR=/system/lib/modules/z1-radio
[ -n "$(getprop init.svc.mnld)" ] || die "init service mnld required; z1_mnld is not compatible with stock HAL"
[ -x /vendor/bin/mnld ] || die "missing executable mnld"
[ -f "$BASE/z1_gpsdrv/z1_gpsdrv.ko" ] || die "missing gpsdrv module source"
[ -f "$BASE/android.hardware.location.gps.xml" ] || die "missing GPS feature declaration"
[ -f "$BASE/z1_gps_prep.sh.patched" ] || die "missing preparation script"
SYSTEM_RW=1
mount -o remount,rw /system || die "cannot remount /system writable"
mkdir -p "$MODDIR" || die "cannot create module directory"
cp "$BASE/z1_gpsdrv/z1_gpsdrv.ko" "$MODDIR/z1_gpsdrv.ko" \
    && chmod 0644 "$MODDIR/z1_gpsdrv.ko" || die "cannot install gpsdrv module"
cp "$BASE/android.hardware.location.gps.xml" /system/etc/permissions/ \
    && chmod 0644 /system/etc/permissions/android.hardware.location.gps.xml \
    || die "cannot install GPS feature declaration"
cmp -s "$BASE/z1_gpsdrv/z1_gpsdrv.ko" "$MODDIR/z1_gpsdrv.ko" \
    || die "gpsdrv module readback mismatch"
cmp -s "$BASE/android.hardware.location.gps.xml" \
    /system/etc/permissions/android.hardware.location.gps.xml \
    || die "GPS feature declaration readback mismatch"
# Use the same fail-closed checks as the boot entrypoint; do not fake init.svc.*.
sh "$BASE/z1_gps_prep.sh.patched" || die "GPS preparation failed; inspect z1gps log"
log "files installed, read back, and daemon state checked; no satellite or location verification performed"
exit 0

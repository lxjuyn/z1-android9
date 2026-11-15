#!/system/bin/sh
# Install the gpsdrv compatibility shim and request GPS preparation.
# Usage: sh install_gps.sh [A]. This is not a complete GPS implementation.
log() { printf '<6>[z1gpsinst] %s\n' "$*" > /dev/kmsg 2>/dev/null || true; }
die() { log "ERROR: $*"; exit 1; }
SYSTEM_MODE=
SYSTEM_NEEDS_RO=0
restore_system_ro() {
    [ "$SYSTEM_NEEDS_RO" = 1 ] || return 0
    if ! mount -o remount,ro /system; then
        log "ERROR: cannot restore /system read-only"
        return 1
    fi
    SYSTEM_NEEDS_RO=0
    return 0
}
cleanup() {
    status=$?
    trap - 0 HUP INT TERM
    restore_system_ro || { [ "$status" -ne 0 ] || status=1; }
    exit "$status"
}
trap 'cleanup' 0
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
SYSTEM_MOUNTS=0
SYSTEM_MODE=
while IFS=' ' read -r mount_device mount_target mount_type mount_options mount_rest; do
    [ "$mount_target" = /system ] || continue
    SYSTEM_MOUNTS=$((SYSTEM_MOUNTS + 1))
    case ",$mount_options," in
        *,ro,*) has_ro=1 ;;
        *) has_ro=0 ;;
    esac
    case ",$mount_options," in
        *,rw,*) has_rw=1 ;;
        *) has_rw=0 ;;
    esac
    [ "$has_ro" -ne "$has_rw" ] || die "ambiguous /system mount mode"
    if [ "$has_ro" = 1 ]; then
        SYSTEM_MODE=ro
    else
        SYSTEM_MODE=rw
    fi
done < /proc/mounts
[ "$SYSTEM_MOUNTS" -eq 1 ] || die "expected exactly one /system mount"

if [ "$SYSTEM_MODE" = ro ]; then
    mount -o remount,rw /system || die "cannot remount /system writable"
    SYSTEM_NEEDS_RO=1
fi
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
# Restore an originally read-only system before starting mnld preparation.
restore_system_ro || die "cannot restore /system read-only"
# Use the same fail-closed checks as the boot entrypoint; do not fake init.svc.*.
sh "$BASE/z1_gps_prep.sh.patched" || die "GPS preparation failed; inspect z1gps log"
log "files installed, read back, and daemon state checked; no satellite or location verification performed"
exit 0

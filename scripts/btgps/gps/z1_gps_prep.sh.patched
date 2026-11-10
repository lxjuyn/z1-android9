#!/system/bin/sh
# Stock mnld reads GPS_CHIP.cfg as raw bytes, not key=value text:
# mnld 53044bfe42868f4b... at 0x2c9e skips detection for a nonempty file;
# 0x2120/0x2174 compare chip strings; 0x226a execl()s the selected helper.
# A registered driver/node and a running daemon do not prove a GPS fix.
log() { printf '<6>[z1gps] %s\n' "$*" > /dev/kmsg 2>/dev/null || true; }
die() { log "ERROR: $*"; exit 1; }

if [ ! -d /data/misc ]; then
    mkdir -p /data/misc && chown 1000:1000 /data/misc && chmod 0771 /data/misc \
        || die "cannot create mnld data directory"
fi
CFG=/data/misc/GPS_CHIP.cfg
chip=
[ ! -L "$CFG" ] || die "refusing symlink GPS_CHIP.cfg"
if [ -e "$CFG" ]; then
    [ -f "$CFG" ] && [ -r "$CFG" ] || die "GPS_CHIP.cfg is not a readable regular file"
    size=$(wc -c < "$CFG") || die "cannot measure GPS_CHIP.cfg"
    [ "$size" -lt 100 ] || die "GPS_CHIP.cfg exceeds stock reader bound; left untouched"
    raw=$(od -An -v -tx1 "$CFG") || die "cannot read GPS_CHIP.cfg"
    hex=$(printf '%s' "$raw" | tr -d '[:space:]') || die "cannot decode GPS_CHIP.cfg"
    case "$hex" in
        # Exact bytes of the seed previously emitted by this project, including LF.
        4d544b5f4750535f434849503d4d54363632300a)
            stamp=$(date +%Y%m%d_%H%M%S) || die "cannot timestamp config backup"
            bad="$CFG.bad-$stamp-$$"
            [ ! -e "$bad" ] && [ ! -L "$bad" ] || die "config backup already exists"
            mv "$CFG" "$bad" || die "cannot archive known-bad config; daemon not started"
            log "archived known-bad config; mnld must detect its own chip"
            ;;
        '') ;; # Empty/absent config permits stock autodetection; do not guess a chip.
        307836363230|30783636323000*) chip=6620 ;;
        307836363238|30783636323800*) chip=6628 ;;
        307836363330|30783636333000*) chip=6630 ;;
        307836353732|30783635373200*) chip=6572 ;;
        307836353731|30783635373100*) chip=6571 ;;
        307836353832|30783635383200*) chip=6582 ;;
        307836353932|30783635393200*) chip=6592 ;;
        307833333332|30783333333200*) chip=3332 ;;
        *) die "unrecognised GPS_CHIP.cfg bytes; left untouched, no chip forced" ;;
    esac
    if [ -e "$CFG" ]; then
        chown 1000:1000 "$CFG" && chmod 0600 "$CFG" || die "cannot set config access for mnld"
    fi
fi

# Stock HAL polls init.svc.mnld.  Do not forge this property or silently use
# z1_mnld: renaming the public init service is an external integration step.
state=$(getprop init.svc.mnld) || die "cannot read mnld service state"
if [ -z "$state" ]; then
    old_state=$(getprop init.svc.z1_mnld) || die "cannot read legacy service state"
    [ -z "$old_state" ] || die "only z1_mnld exists; stock HAL requires init service mnld"
    die "init service mnld is not defined"
fi
[ -x /vendor/bin/mnld ] || die "missing executable /vendor/bin/mnld"
if [ -n "$chip" ]; then
    [ -x "/system/xbin/libmnlp_mt$chip" ] || die "configured chip has no matching executable helper"
fi

MODDIR=/system/lib/modules/z1-radio
GPSDRV_KO=$MODDIR/z1_gpsdrv.ko
if [ ! -d /sys/class/gpsdrv/gps ]; then
    [ -f "$GPSDRV_KO" ] || die "missing gpsdrv module"
    err=$(insmod "$GPSDRV_KO" 2>&1) || die "gpsdrv insmod failed: $err"
fi
# Check both preloaded and newly loaded shim attributes. This caller's access
# check does not verify mnld's SELinux domain or implement power/suspend.
for attr in status state pwrctl suspend pwrsave; do
    [ -r "/sys/class/gpsdrv/gps/$attr" ] && [ -w "/sys/class/gpsdrv/gps/$attr" ] \
        || die "gpsdrv attribute missing or inaccessible: $attr"
done

registered_major() {
    awk '/^Character devices:/{c=1;next} /^Block devices:/{c=0} \
        c && $2 == "mtk_stp_GPS_chrdev" {print $1}' /proc/devices
}
major=$(registered_major) || die "cannot read character driver registry"
if [ -z "$major" ]; then
    [ -f "$MODDIR/mtk_stp_gps_soc.ko" ] || die "missing STP GPS module"
    err=$(insmod "$MODDIR/mtk_stp_gps_soc.ko" 2>&1) || die "STP GPS insmod failed: $err"
    major=$(registered_major) || die "cannot reread character driver registry"
fi
case "$major" in ''|*[!0-9]*) die "STP GPS character driver is not uniquely registered" ;; esac
[ "$major" -gt 0 ] || die "invalid registered GPS major"
# Driver uses cdev_add(), not device_create(); major defaults to 191 but is
# configurable. Use its actual registered major, and verify minor zero too.
NODE=/dev/stpgps
[ ! -L "$NODE" ] || die "refusing symlink stpgps node"
if [ ! -e "$NODE" ]; then
    mknod "$NODE" c "$major" 0 || die "cannot create stpgps node"
fi
test -c "$NODE" || die "stpgps is not a character device"
rdev=$(stat -c '%t:%T' "$NODE") || die "cannot read stpgps device numbers"
expected=$(printf '%x:0' "$major") || die "cannot format GPS major"
[ "$rdev" = "$expected" ] || die "stpgps major/minor mismatch; existing node left untouched"
chmod 0660 "$NODE" && chown 1000:1000 "$NODE" || die "cannot set stpgps permissions"

if [ "$state" != running ]; then
    start mnld || die "init rejected start mnld"
fi
i=0
while [ "$i" -lt 5 ]; do
    state=$(getprop init.svc.mnld) || die "cannot read mnld state after start"
    if [ "$state" = running ]; then
        log "mnld reports running; satellites, NMEA and location callbacks remain unverified"
        exit 0
    fi
    sleep 1 || die "service wait interrupted"
    i=$((i + 1))
done
die "mnld did not report running within 5s (state=$state)"

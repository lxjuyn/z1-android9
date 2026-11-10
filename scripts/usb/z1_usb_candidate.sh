#!/system/bin/sh
# NOT_DEPLOYED. Candidate for a separately reviewed image, never run on this host.
# No default mode, host fixture mode, generic USB properties or existing ACM edits.
# The offline checker verifies the imported adbd service, ELF and auth socket.
# This runtime guard rechecks live properties, sole MUSB and FunctionFS readiness.
# SELinux policy for z1_usb_candidate is a separate unmet deployment prerequisite.

PATH=/system/bin:/system/xbin
export PATH
G=/config/usb_gadget/z1_candidate
FFS=/dev/usb-ffs/adb
OWNER=/dev/.z1-usb-candidate-owner
LOCK=/dev/.z1-usb-candidate-lock

fail() { echo "z1-usb-candidate: BLOCKED: $*" >&2; exit 1; }
prop() { getprop "$1"; }
put() { printf '%s\n' "$2" > "$1" || fail "write failed: $1"; }

case "$1" in prepare|bind|stop) MODE=$1 ;; *) fail 'expected prepare, bind or stop' ;; esac
[ "$#" -eq 1 ] || fail 'unexpected arguments'
[ "$(prop ro.z1.usb.candidate)" = 1 ] || fail 'explicit opt-in absent'
# Property actions can dispatch the next helper before the previous process
# releases its lock. Wait briefly instead of losing a one-shot bind/stop event.
lock_attempt=0
while ! mkdir "$LOCK" 2>/dev/null; do
    [ "$lock_attempt" -lt 5 ] || fail 'candidate lock timeout; require explicit retry'
    sleep 1
    lock_attempt=$((lock_attempt + 1))
done
trap 'rmdir "$LOCK" 2>/dev/null' EXIT
trap 'exit 1' HUP INT TERM

# Only the candidate itself can tear down its own gadget. This never touches
# z1diag, g1, ttyGS0, the ACM logger or persisted USB configuration.
if [ "$MODE" = stop ]; then
    [ -f "$OWNER" ] && [ "$(cat "$OWNER")" = z1_candidate ] || fail 'no candidate ownership marker'
    setprop sys.z1.usb.candidate.prepared 0 || fail 'clear prepared'
    if [ -f "$G/UDC" ]; then
        bound=$(cat "$G/UDC") || fail 'read candidate UDC'
        [ -z "$bound" ] || put "$G/UDC" ''
    fi
    setprop sys.z1.usb.candidate.bound 0 || fail 'clear bound'
    # Stop only after withdrawing startup triggers and unbinding our gadget.
    setprop ctl.stop adbd || fail 'stop candidate adbd'
    # Keep the mount/function objects owned: no blind umount/rmdir while adbd
    # is exiting. A fresh boot is required before preparing another candidate.
    exit 0
fi

[ "$(prop sys.z1.usb.candidate.request)" = acm,adb ] || fail 'ACM+ADB not requested'
[ "$(prop ro.adb.secure)" = 1 ] || fail 'ADB authentication is not enabled'
[ "$(prop ro.secure)" = 1 ] || fail 'secure privilege dropping is not enabled'
[ "$(prop service.adb.root)" != 1 ] || fail 'root ADB requested'
[ "$(prop ro.kernel.qemu)" != 1 ] || fail 'emulator authentication path forbidden'
for key in service.adb.tcp.port persist.adb.tcp.port; do
    case "$(prop "$key")" in ''|0|-1) ;; *) fail 'network ADB must be disabled' ;; esac
done
[ -r /sys/fs/selinux/enforce ] && [ "$(cat /sys/fs/selinux/enforce)" = 1 ] || fail 'enforcing SELinux required'
[ -x /system/bin/adbd ] || fail 'adbd executable absent'
adbd_state=$(prop init.svc.adbd)
case "$adbd_state" in stopped|running) ;; *) fail 'adbd service missing or transitioning' ;; esac
case "$(prop init.svc.z1_acm_log)" in ''|stopped) ;; *) fail 'current ACM owner is active' ;; esac

# Permit the exact MUSB family; never select the first arbitrary controller.
controller=
count=0
for node in /sys/class/udc/*; do
    [ -d "$node" ] || continue
    count=$((count + 1))
    name=${node##*/}
    case "$name" in
        musb-hdrc) ;;
        musb-hdrc.*.auto)
            suffix=${name#musb-hdrc.}; suffix=${suffix%.auto}
            case "$suffix" in ''|*[!0-9]*) fail 'unknown MUSB controller name' ;; esac ;;
        *) fail 'unknown UDC controller' ;;
    esac
    driver=$(readlink "$node/device/driver") || fail 'MUSB driver identity unavailable'
    [ "${driver##*/}" = musb-hdrc ] || fail 'UDC driver is not MUSB'
    controller=$name
done
[ "$count" -eq 1 ] || fail 'exactly one MUSB controller required'
# Existing bound ACM is a hard conflict, not permission to steal its controller.
for udc in /config/usb_gadget/*/UDC; do
    [ -e "$udc" ] || continue
    value=$(cat "$udc") || fail 'cannot inspect gadget ownership'
    [ -z "$value" ] || fail 'UDC is already owned by a gadget'
done
awk '$2=="/config" && $3=="configfs" {found=1} END {exit !found}' /proc/mounts || fail 'configfs is not mounted at /config'

if [ "$MODE" = prepare ]; then
    [ "$adbd_state" = stopped ] || fail 'adbd must be stopped before preparation'
    [ ! -e "$G" ] && [ ! -e "$OWNER" ] || fail 'candidate already exists; require fresh boot'
    # Even an unbound foreign ffs.adb instance conflicts with the mount name.
    for instance in /config/usb_gadget/*/functions/ffs.adb; do
        [ ! -e "$instance" ] || fail 'another ADB FunctionFS instance exists'
    done
    awk '$2=="/dev/usb-ffs/adb" {found=1} END {exit found}' /proc/mounts || fail 'ADB FunctionFS already mounted'
    setprop sys.z1.usb.candidate.prepared 0 || fail 'clear preparation'
    setprop sys.z1.usb.candidate.bound 0 || fail 'clear binding'
    setprop sys.usb.ffs.ready 0 || fail 'clear stale ADB readiness'
    mkdir "$G" || fail 'create candidate gadget'
    # Mark ownership before subsequent writes: a partial failure stays unbound.
    put "$OWNER" z1_candidate
    mkdir -p "$G/strings/0x409" "$G/configs/c.1/strings/0x409" \
        "$G/functions/acm.usb0" "$G/functions/ffs.adb" || fail 'create functions'
    # Same local laboratory VID/PID as the existing diagnostic implementation.
    put "$G/idVendor" 0x0e8d
    put "$G/idProduct" 0x2006
    put "$G/bcdUSB" 0x0200
    put "$G/bcdDevice" 0x0100
    serial=$(prop ro.serialno)
    [ -n "$serial" ] || fail 'device serial missing'
    put "$G/strings/0x409/serialnumber" "$serial"
    put "$G/strings/0x409/manufacturer" Z1
    put "$G/strings/0x409/product" 'Z1 ACM ADB candidate'
    put "$G/configs/c.1/strings/0x409/configuration" 'ACM and authenticated ADB'
    put "$G/configs/c.1/MaxPower" 100
    ln -s "$G/functions/acm.usb0" "$G/configs/c.1/acm.usb0" || fail 'link ACM'
    mkdir -p "$FFS" || fail 'create FunctionFS mountpoint'
    mount -t functionfs -o uid=2000,gid=2000,rmode=0770,fmode=0660 adb "$FFS" || fail 'mount FunctionFS'
    # No ep0/ready test before start: adbd writes descriptors after this signal.
    setprop sys.z1.usb.candidate.controller "$controller" || fail 'record controller'
    setprop sys.z1.usb.candidate.prepared 1 || fail 'request adbd startup'
    exit 0
fi

[ -f "$OWNER" ] && [ "$(cat "$OWNER")" = z1_candidate ] || fail 'candidate ownership missing'
[ "$(prop sys.z1.usb.candidate.prepared)" = 1 ] || fail 'candidate not prepared'
[ "$(prop sys.z1.usb.candidate.controller)" = "$controller" ] || fail 'controller changed'
[ "$adbd_state" = running ] || fail 'adbd is not running'
[ -S /dev/socket/adbd ] || fail 'adbd authentication socket absent'
[ "$(prop sys.usb.ffs.ready)" = 1 ] || fail 'adbd descriptors not ready'
awk '$1=="adb" && $2=="/dev/usb-ffs/adb" && $3=="functionfs" {found=1} END {exit !found}' /proc/mounts || fail 'ADB FunctionFS mount absent'
[ -f "$FFS/ep0" ] || fail 'FunctionFS ep0 absent or wrong type'
[ "$(cat "$G/functions/ffs.adb/ready" 2>/dev/null)" = 1 ] || fail 'kernel FunctionFS descriptors not ready'
[ "$(readlink "$G/configs/c.1/acm.usb0")" = "$G/functions/acm.usb0" ] || fail 'ACM link changed'
ln -s "$G/functions/ffs.adb" "$G/configs/c.1/ffs.adb" || fail 'link ADB'
# Recheck descriptor state immediately before the only nonempty UDC write.
[ "$(prop init.svc.adbd)" = running ] && \
    [ "$(prop sys.usb.ffs.ready)" = 1 ] && \
    [ "$(cat "$G/functions/ffs.adb/ready" 2>/dev/null)" = 1 ] && \
    [ "$(prop sys.z1.usb.candidate.request)" = acm,adb ] && \
    [ "$(prop service.adb.root)" != 1 ] || fail 'ADB readiness or request lost'
put "$G/UDC" "$controller"
[ "$(cat "$G/UDC")" = "$controller" ] || fail 'UDC readback differs'
if ! setprop sys.z1.usb.candidate.bound 1; then
    put "$G/UDC" ''
    fail 'record bound state; candidate unbound'
fi
# A daemon exit racing the property update must not leave stale ADB bound.
if [ "$(prop init.svc.adbd)" != running ] || [ "$(prop sys.usb.ffs.ready)" != 1 ] ||
    [ "$(cat "$G/functions/ffs.adb/ready" 2>/dev/null)" != 1 ] ||
    [ "$(prop sys.z1.usb.candidate.request)" != acm,adb ] ||
    [ "$(prop service.adb.root)" = 1 ]; then
    put "$G/UDC" ''
    setprop sys.z1.usb.candidate.bound 0
    fail 'ADB readiness lost after binding; candidate unbound'
fi
# Binding is not enumeration, authorization or working adb shell evidence.

#!/bin/bash
# ---------------------------------------------------------------------------
# z1_install_hal.sh -- install / roll back a graphics HAL on a running Z1,
# over the serial console, without flashing anything.
#
#   z1_install_hal.sh install  <local-file.so> [hwcomposer|gralloc]
#   z1_install_hal.sh rollback [hwcomposer|gralloc|both]
#   z1_install_hal.sh status
#
# What "install" does (all on the device):
#   * push the .so to /data/local/tmp/z1stage/<name>.z1.so via z1push.sh
#     (base64 + device sha256 verification)
#   * copy it into active/<name>.z1.so so a reboot re-applies it
#   * as root: cp to /vendor/lib/hw/<name>.z1.so.z1tmp, chmod 0644, mv -f over
#     the real file (never rewrite an mmap'ed .so in place), then sha256sum
#   * restart the HAL service; for hwcomposer that is
#     `setprop ctl.restart vendor.hwcomposer-2-1`, whose rc also restarts
#     SurfaceFlinger when it stops.  For gralloc it is
#     `setprop ctl.restart vendor.gralloc-2-0`.
#
# "rollback" deletes active/<name>.z1.so and the /vendor override, which makes
# libhardware fall back to /system/lib/hw (the stock goldfish HWC1 composer /
# the stock gralloc).
#
# Environment: Z1LOG (append-only serial capture, required), Z1DEV.
# ---------------------------------------------------------------------------
set -u

HERE=$(cd -P -- "$(dirname -- "$0")" && pwd)
PUSH="$HERE/z1push.sh"
STAGE=/data/local/tmp/z1stage
VENDOR=/vendor/lib/hw
WAIT=${Z1_WAIT:-40}

export Z1DEV=${Z1DEV:-/dev/ttyUSB0}
: "${Z1LOG:?set Z1LOG to the append-only serial capture file}"
export Z1LOG

die() { printf 'z1_install_hal: %s\n' "$*" >&2; exit 1; }

[ -x "$PUSH" ] || die "missing $PUSH"
[ -r "$Z1LOG" ] || die "capture file $Z1LOG not readable"

name_of() {
    case "$1" in
        hwcomposer|hwc) echo hwcomposer.z1.so ;;
        gralloc)        echo gralloc.z1.so ;;
        *) die "unknown HAL '$1' (use hwcomposer or gralloc)" ;;
    esac
}

svc_of() {
    case "$1" in
        hwcomposer.z1.so) echo vendor.hwcomposer-2-1 ;;
        gralloc.z1.so)    echo vendor.gralloc-2-0 ;;
    esac
}

root_cmd() {
    printf '%s\n' "$1" | "$PUSH" --rootcmd - --wait "$WAIT"
}

cmd() {
    "$PUSH" --cmd "$1" --wait "$WAIT"
}

do_status() {
    echo "== installed modules =="
    cmd 'ls -l /vendor/lib/hw/ 2>/dev/null | grep -E "hwcomposer|gralloc"; echo ---; sha256sum /vendor/lib/hw/hwcomposer.z1.so /vendor/lib/hw/gralloc.z1.so /system/lib/hw/hwcomposer.z1.so /system/lib/hw/gralloc.z1.so 2>&1'
    echo "== service state =="
    cmd 'getprop init.svc.vendor.hwcomposer-2-1; getprop init.svc.vendor.gralloc-2-0; getprop z1stage.hwcomposer; getprop z1stage.gralloc'
    echo "== active set =="
    cmd 'ls -l /data/local/tmp/z1stage/active/ 2>&1'
}

do_install() {
    local src=$1 hal=$2 remote sha
    [ -f "$src" ] || die "no such file: $src"
    remote=$(name_of "$hal")
    sha=$(sha256sum "$src" | awk '{print $1}')
    echo "installing $src -> $remote (sha256 $sha)"

    # 0) sanity: it must be a 32-bit ARM shared object exporting HMI
    file "$src" | grep -q "ELF 32-bit LSB shared object, ARM" || die "$src is not an ARM32 shared object"
    arm-linux-gnueabihf-nm -D "$src" 2>/dev/null | grep -q " D HMI" || die "$src does not export the HAL_MODULE_INFO_SYM (HMI) symbol"

    # 1) push
    "$PUSH" --mode 0644 "$src" "$remote" || die "push failed"
    cmd "sha256sum $STAGE/$remote" || true

    # 2) keep it in active/ so the boot script re-applies it after a reboot,
    #    then install into /vendor as root.
    root_cmd "set -e
mkdir -p $STAGE/active
cp -f $STAGE/$remote $STAGE/active/$remote
chmod 0644 $STAGE/active/$remote
chown 2000:2000 $STAGE/active/$remote 2>/dev/null || true
cp -f $STAGE/active/$remote $VENDOR/$remote.z1tmp
chmod 0644 $VENDOR/$remote.z1tmp
mv -f $VENDOR/$remote.z1tmp $VENDOR/$remote
sha256sum $VENDOR/$remote" || die "root install failed (is z1_rootd running?)"

    # 3) restart the service that owns the HAL
    local svc
    svc=$(svc_of "$remote")
    cmd "setprop ctl.restart $svc" || true
    echo "restarted $svc; waiting 6 s for the HAL to come up"
    sleep 6
    cmd 'dmesg | grep -a -E "z1hwc2|z1_heap|gralloc" | tail -25; getprop init.svc.vendor.hwcomposer-2-1; getprop init.svc.surfaceflinger' || true
}

do_rollback() {
    local which=$1 remote svc
    if [ "$which" = both ]; then
        do_rollback hwcomposer
        do_rollback gralloc
        return 0
    fi
    remote=$(name_of "$which")
    svc=$(svc_of "$remote")
    echo "rolling back $remote (removing the /vendor override and the active copy)"
    root_cmd "rm -f $STAGE/active/$remote
rm -f $VENDOR/$remote
ls -l $VENDOR/ | grep -E 'hwcomposer|gralloc' || true
getprop | grep -E 'z1stage' | head -5" || die "root rollback failed"
    cmd "setprop ctl.restart $svc" || true
    echo "restarted $svc; stock /system/lib/hw/$remote is now in use"
}

case "${1:-}" in
    install)  [ $# -ge 2 ] || die "usage: $0 install <file.so> [hwcomposer|gralloc]"; do_install "$2" "${3:-hwcomposer}" ;;
    rollback) do_rollback "${2:-hwcomposer}" ;;
    status)   do_status ;;
    *) sed -n '2,30p' "$0"; exit 2 ;;
esac

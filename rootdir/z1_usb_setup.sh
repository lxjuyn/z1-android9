#!/system/bin/sh
# MUSB's .N.auto suffix depends on platform device probe ordering.
# Generic init.usb.configfs.rc owns adbd startup and binding after FFS readiness.

usb_fail() {
    echo "<3>[z1-usb] $*" > /dev/kmsg
    exit 1
}

[ -e /dev/usb-ffs/adb/ep0 ] || usb_fail "FunctionFS ep0 missing; ADB setup aborted"
[ -d /config/usb_gadget/g1/functions/ffs.adb ] || usb_fail "ffs.adb missing; ADB setup aborted"

usb_controller=
usb_attempt=0
while [ "$usb_attempt" -lt 20 ]; do
    for usb_path in /sys/class/udc/*; do
        [ -d "$usb_path" ] || continue
        case "${usb_path##*/}" in
            musb-hdrc*) usb_controller=${usb_path##*/}; break ;;
        esac
    done
    [ -n "$usb_controller" ] && break
    sleep 1
    usb_attempt=$((usb_attempt + 1))
done

[ -n "$usb_controller" ] || usb_fail "No MUSB UDC after 20 seconds; ADB setup aborted"
setprop sys.usb.controller "$usb_controller" || usb_fail "Cannot set USB controller"
setprop sys.usb.configfs 1 || usb_fail "Cannot select configfs"
setprop sys.usb.config adb || usb_fail "Cannot request ADB"
echo "<6>[z1-usb] ADB requested on $usb_controller; waiting for adbd FunctionFS readiness" > /dev/kmsg

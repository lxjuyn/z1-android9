#!/system/bin/sh
# Passive Bluetooth transport probe with bounded output.
# It never enables/disables Bluetooth, writes settings, loads modules, or
# reads the deployment daemon's output file.
set -u
PATH=/system/bin:/system/xbin:/vendor/bin:/sbin:$PATH
export PATH

say() { printf '[z1diag-bt-min] %s\n' "$*"; }
cap() {
    _label=$1; shift
    printf '\n=== %s\n' "$_label"
    "$@" 2>&1 | head -n 60
    printf '=== end %s\n' "$_label"
}

say 'BEGIN passive=1 bounded=1'
cap transport_nodes ls -l /dev/stpbt /dev/stpwmt
cap modules grep -iE 'stp_bt|btif|stp_wmt|wmt' /proc/modules
cap bluetooth_sysfs ls -l /sys/class/bluetooth
if command -v hciconfig >/dev/null 2>&1; then
    cap hciconfig hciconfig -a
else
    say 'hciconfig unavailable'
fi
cap bluetooth_service getprop init.svc.bluetooth
cap bluetooth_setting settings get global bluetooth_on
if command -v dmesg >/dev/null 2>&1; then
    printf '\n=== dmesg_bt_tail\n'
    dmesg 2>/dev/null | grep -iE 'btif|stpbt|bluetooth|hci|stp_bt|stp_wmt|wmt' | tail -n 60
    printf '=== end dmesg_bt_tail\n'
else
    say 'dmesg unavailable'
fi
say 'END passive=1'
exit 0

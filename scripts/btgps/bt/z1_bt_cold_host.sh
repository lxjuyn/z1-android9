#!/usr/bin/env bash
# Observe one Android Bluetooth enable from a cold boot with Wi-Fi disabled.
set -euo pipefail

usage() {
    cat <<'USAGE'
usage: z1_bt_cold_host.sh [--check-capture|--dry-run]

Z1LOG must identify the UART capture started before applying board power.
Z1DEV and Z1_BAUD select the UART using the regular z1push.sh defaults.
Start the board with both Android radio settings off. Leave Settings and any
raw radio probes alone until this test finishes. Use a private local UART log.
On a running preparatory boot, use Settings to turn both radios off and
shut down normally. Their saved networks and pairings can stay in place.
--check-capture only checks historical UART evidence, without contacting the Z1.
--dry-run displays the root script, without contacting the Z1.
USAGE
}

case "${1:-}" in
    -h|--help) usage; exit 0 ;;
    ''|--check-capture|--dry-run) ;;
    *) usage >&2; exit 2 ;;
esac
[[ $# -le 1 ]] || { usage >&2; exit 2; }
here=$(cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
push="$here/../../z1deploy/z1push.sh"

# Check only the most recent boot in the capture. Examine the entire boot's
# serial history because Android's dmesg ring may have wrapped by boot_completed.
if [[ ${1:-} != --dry-run ]]; then
    [[ -n ${Z1LOG:-} && -f $Z1LOG ]] || {
        printf 'Z1LOG must name the capture started before power-on.\n' >&2
        exit 2
    }
    LC_ALL=C awk '
        /^\[ *0\.000000\] Linux version .*z1/ {
            boot = NR
            prior = 0
        }
        boot && /^\[ *[0-9]+\.[0-9]+\]/ &&
            /BT_open:|BLE_TURNING_ON|wmt_func_ctrl_cmd 0x0 FAIL|WMT turn on WIFI|function-on written to \/dev\/wmtWifi|\[z1wifi\] probe start|\[z1btgate\] begin|\[z1btcold\] request / {
                if (!prior) prior = NR
            }
        END {
            if (!boot) {
                print "BLOCKED: no Z1 kernel start in the UART capture."
                exit 2
            }
            if (prior) {
                printf "BLOCKED: prior radio activity at UART line %d (boot at %d).\n", prior, boot
                exit 2
            }
            printf "UART preflight: Z1 boot starts at line %d.\n", boot
        }
    ' "$Z1LOG"
fi
[[ ${1:-} != --check-capture ]] || exit 0

device_script=$(cat <<'DEVICE'
set -u
report() { printf '[z1btcold] %s\n' "$*"; }
block() { report "RESULT=BLOCKED reason=$1"; exit 2; }
[ "$(id -u)" = 0 ] || block not_root
boot_id=$(cat /proc/sys/kernel/random/boot_id) || block boot_id
case "$boot_id" in ''|*[!a-f0-9-]*) block boot_id ;; esac
[ "$(getprop sys.boot_completed)" = 1 ] || block boot_incomplete
[ "$(getprop init.svc.z1_6620)" = running ] || block launcher
[ "$(getprop init.svc.vendor.bluetooth-1-0)" = running ] || block bluetooth_hal
[ "$(getprop sys.z1.radio.bt_nvram_ready)" = 1 ] || block nvram
[ -c /dev/stpbt ] && [ -c /dev/stpwmt ] || block transport_nodes
[ "$(settings get global wifi_on)" = 0 ] || block android_wifi_setting
[ ! -e /sys/class/net/wlan0 ] || block wlan0_present
[ "$(settings get global bluetooth_on)" = 0 ] || block android_bluetooth_setting

adapter_state() {
    timeout 8 dumpsys bluetooth_manager 2>/dev/null |
        awk '/^  state: (OFF|ON|TURNING_ON|TURNING_OFF|BLE_ON|BLE_TURNING_ON|BLE_TURNING_OFF)$/ && !seen++ {print $2}'
}
[ "$(adapter_state)" = OFF ] || block adapter_not_confirmed_off

# Catch a prior opener or radio activity that reached the kernel while the
# host preflight was running. A HAL daemon can be idle with no device open.
for fd in /proc/[0-9]*/fd/*; do
    [ "$(readlink "$fd" 2>/dev/null)" != /dev/stpbt ] || block stpbt_already_open
done
history=$(dmesg 2>/dev/null) || block dmesg
if printf '%s\n' "$history" |
    grep -qE 'BT_open:|WMT turn on WIFI|function-on written to /dev/wmtWifi|\[z1wifi\] probe start|\[z1btgate\] begin'; then
    block prior_kernel_radio_activity
fi
unset history
mkdir "/dev/.z1btcold-$boot_id" 2>/dev/null || block already_attempted

uptime=$(cut -d ' ' -f 1 /proc/uptime) || block uptime
report "begin boot_id=$boot_id uptime=$uptime wifi_setting=0 wlan0=absent adapter=OFF"
for lib in /vendor/lib/libwifi-hal.so /vendor/lib/libbt-vendor.so /system/lib/libbluetooth.so; do
    if [ -f "$lib" ]; then
        report "identity $(sha256sum "$lib" 2>/dev/null)"
    fi
done
printf '<6>[z1btcold] request boot_id=%s\n' "$boot_id" > /dev/kmsg || block marker
timeout 10 svc bluetooth enable >/dev/null 2>&1
enable_rc=$?
report "enable_request_rc=$enable_rc"
for sample in 1 2 3 4 5 6 7 8 9; do
    sleep 5
    state=$(adapter_state)
    uptime=$(cut -d ' ' -f 1 /proc/uptime)
    report "sample=$sample uptime=$uptime state=${state:-UNKNOWN}"
    case "$sample" in
        8) before_final=$state ;;
        9) final=$state ;;
    esac
done
[ "$boot_id" = "$(cat /proc/sys/kernel/random/boot_id)" ] || block rebooted
window=$(dmesg 2>/dev/null | sed -n "/\[z1btcold\] request boot_id=$boot_id/,\$p")
[ -n "$window" ] || block request_marker_missing
events=$(printf '%s\n' "$window" |
    grep -E 'BT_open: WMT turn on BT (OK|fail)!|wmt_func_bt_on.*failed|wmt_dev_rx_timeout.*reset it|Whole chip reset start|WMT turn on WIFI|_stp_psm_wait_wmt_event_wq' || true)
printf '%s\n' "$events" | while IFS= read -r line; do
    [ -z "$line" ] || report "kernel $line"
done
if [ "$enable_rc" != 0 ]; then
    report 'RESULT=FAIL reason=enable_request'
elif printf '%s\n' "$events" | grep -qiE 'BT_open: WMT turn on BT fail!|wmt_func_bt_on.*failed|wmt_dev_rx_timeout|Whole chip reset start|WMT turn on WIFI|_stp_psm_wait_wmt_event_wq' ||
    [ -e /sys/class/net/wlan0 ]; then
    report 'RESULT=FAIL reason=radio_failure_reset_or_wifi_activity'
elif [ "$(printf '%s\n' "$events" | grep -c 'BT_open: WMT turn on BT OK!')" != 1 ]; then
    report 'RESULT=FAIL reason=expected_one_wmt_bt_open_success'
elif [ "$before_final" != ON ] || [ "$final" != ON ]; then
    report 'RESULT=FAIL reason=adapter_not_stably_on'
else
    report 'RESULT=PASS'
fi
DEVICE
)

if [[ ${1:-} == --dry-run ]]; then
    "$push" --dry-run --wait 175 --rootcmd "$device_script"
    exit
fi

# The UART capture is already written by the operator's capture process.
# Only display the marker lines emitted by this script.
output=$("$push" --wait 175 --rootcmd "$device_script")
printf '%s\n' "$output" | sed -n '/^\[z1btcold\] /p'
if ! printf '%s\n' "$output" | grep -qx '\[z1btcold\] RESULT=PASS'; then
    exit 1
fi

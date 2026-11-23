#!/system/bin/sh
# Run one exclusive raw-H4 transport mode on an otherwise idle Z1.
set -u

PATH=/system/bin:/system/xbin:/vendor/bin:/sbin:$PATH
export PATH
umask 022

MODE=${1:-}
PROBE=${2:-/data/local/tmp/z1stage/z1_bt_hci_probe}
EXPECTED_PROBE_SHA=${3:-}
STATE=/data/local/tmp/z1_bt_transport_boot_state

usage() {
    echo "usage: $0 <write|writev> [probe] [expected_sha256]" >&2
}

fail() {
    echo "TRANSPORT_RESULT=FAIL reason=$1"
    exit 1
}

run_limited() {
    limit=$1
    shift
    command -v timeout >/dev/null 2>&1 || {
        echo "timeout command is required" >&2
        return 127
    }
    timeout "$limit" "$@"
}

identity() {
    if [ -f "$1" ]; then
        sha256sum "$1" 2>/dev/null | awk '{print $1}'
    else
        echo MISSING
    fi
}

first_identity() {
    path=$1
    for candidate in $path; do
        if [ -f "$candidate" ]; then
            identity "$candidate"
            return
        fi
    done
    echo MISSING
}

scan_openers() {
    found=
    for fd in /proc/[0-9]*/fd/*; do
        [ -L "$fd" ] || continue
        target=$(readlink "$fd" 2>/dev/null) || continue
        [ "$target" = /dev/stpbt ] || continue
        pid=${fd#/proc/}
        pid=${pid%%/*}
        case " $found " in
            *" $pid "*) ;;
            *) found="$found $pid" ;;
        esac
    done
    printf '%s\n' "$found"
}

case "$MODE" in
    write|writev) ;;
    *)
        usage
        exit 2
        ;;
esac

[ "$(id -u 2>/dev/null)" = 0 ] || fail not_root
[ -x "$PROBE" ] || fail probe_not_executable
[ -c /dev/stpbt ] || fail stpbt_missing
command -v timeout >/dev/null 2>&1 || fail timeout_missing
command -v sha256sum >/dev/null 2>&1 || fail sha256sum_missing

PROBE_SHA=$(identity "$PROBE")
if [ -n "$EXPECTED_PROBE_SHA" ] && [ "$PROBE_SHA" != "$EXPECTED_PROBE_SHA" ]; then
    echo "expected_probe_sha256=$EXPECTED_PROBE_SHA"
    echo "actual_probe_sha256=$PROBE_SHA"
    fail probe_sha_mismatch
fi

BOOT_ID=$(cat /proc/sys/kernel/random/boot_id 2>/dev/null)
[ -n "$BOOT_ID" ] || fail boot_id_unavailable
UPTIME=$(cut -d. -f1 /proc/uptime 2>/dev/null)
[ -n "$UPTIME" ] || UPTIME=unknown
TS=$(date +%Y%m%d_%H%M%S 2>/dev/null)
[ -n "$TS" ] || TS=$UPTIME
OUT=/data/local/tmp/z1_bt_transport_${MODE}_$TS
mkdir -p "$OUT" || fail output_dir

old_boot=
old_mode=
if [ -f "$STATE" ]; then
    read -r old_boot old_mode _ < "$STATE" || true
fi
if [ "$old_boot" = "$BOOT_ID" ] && [ "$old_mode" != "$MODE" ]; then
    echo "previous_mode=$old_mode"
    echo "current_mode=$MODE"
    echo "boot_id=$BOOT_ID"
    fail same_boot_transport_comparison
fi
state_tmp="$STATE.$$"
printf '%s %s\n' "$BOOT_ID" "$MODE" > "$state_tmp" || fail state_write
mv -f "$state_tmp" "$STATE" || fail state_replace

echo "TRANSPORT_BEGIN mode=$MODE"
echo "boot_id=$BOOT_ID"
echo "uptime_s=$UPTIME"
echo "output_dir=$OUT"
echo "probe=$PROBE"
echo "probe_sha256=$PROBE_SHA"
echo "kernel=$(uname -r 2>/dev/null)"
echo "build=$(getprop ro.build.fingerprint 2>/dev/null)"
echo "boot_completed=$(getprop sys.boot_completed 2>/dev/null)"
echo "nvram_init=$(getprop nvram_init 2>/dev/null)"
echo "bt_nvram_ready=$(getprop sys.z1.radio.bt_nvram_ready 2>/dev/null)"
echo "combo_chipid=$(getprop persist.mtk.wcn.combo.chipid 2>/dev/null)"
echo "vendor_adapter_sha256=$(first_identity '/vendor/lib/libbt-vendor.so /system/vendor/lib/libbt-vendor.so')"
echo "libbluetooth_sha256=$(first_identity '/system/lib/libbluetooth.so /lib/libbluetooth.so')"
echo "stp_bt_module_sha256=$(first_identity '/lib/modules/z1-radio/mtk_stp_bt_soc.ko /system/lib/modules/z1-radio/mtk_stp_bt_soc.ko /lib/modules/mtk_stp_bt_soc.ko')"
echo "stp_wmt_module_sha256=$(first_identity '/lib/modules/z1-radio/mtk_stp_wmt_soc.ko /system/lib/modules/z1-radio/mtk_stp_wmt_soc.ko /lib/modules/mtk_stp_wmt_soc.ko')"
echo "btif_module_sha256=$(first_identity '/lib/modules/z1-radio/mtk_btif_drv.ko /system/lib/modules/z1-radio/mtk_btif_drv.ko /lib/modules/mtk_btif_drv.ko')"
grep -E '^(mtk_stp_bt_soc|mtk_stp_wmt_soc|mtk_btif_drv) ' /proc/modules 2>/dev/null |
    while IFS= read -r line; do echo "module_loaded=$line"; done

# Disable the framework and HCI HAL without changing the package enable state.
run_limited 20 svc bluetooth disable >/dev/null 2>&1 || true
run_limited 20 am force-stop com.android.bluetooth >/dev/null 2>&1 || true
setprop ctl.stop vendor.bluetooth-1-0 2>/dev/null || true

waited=0
openers=$(scan_openers)
while [ -n "$openers" ] && [ "$waited" -lt 15 ]; do
    sleep 1
    waited=$((waited + 1))
    setprop ctl.stop vendor.bluetooth-1-0 2>/dev/null || true
    openers=$(scan_openers)
done
echo "hci_service=$(getprop init.svc.vendor.bluetooth-1-0 2>/dev/null)"
echo "open_wait_s=$waited"
if [ -n "$openers" ]; then
    echo "stpbt_openers=$openers"
    fail stpbt_still_open
fi
echo "stpbt_openers=none"

printf '<6>[z1btgate] begin mode=%s boot_id=%s\n' "$MODE" "$BOOT_ID" > /dev/kmsg 2>/dev/null || true
probe_rc=0
if [ "$MODE" = writev ]; then
    run_limited 45 "$PROBE" --exclusive-controller --writev > "$OUT/probe.out" 2>&1 || probe_rc=$?
else
    run_limited 45 "$PROBE" --exclusive-controller > "$OUT/probe.out" 2>&1 || probe_rc=$?
fi
printf '<6>[z1btgate] end mode=%s rc=%s\n' "$MODE" "$probe_rc" > /dev/kmsg 2>/dev/null || true

cat "$OUT/probe.out"
echo "probe_rc=$probe_rc"
dmesg 2>/dev/null |
    grep -aE 'z1btgate|BT_open|BT_close|WMT|BTIF|STP|stpbt|reset' |
    tail -n 120 > "$OUT/dmesg.txt"
while IFS= read -r line; do echo "kernel=$line"; done < "$OUT/dmesg.txt"

reset=FAIL
localver=FAIL
grep -qx 'PASS opcode=0x0c03' "$OUT/probe.out" && reset=PASS
grep -qx 'PASS opcode=0x1001' "$OUT/probe.out" && localver=PASS
echo "HCI_RESET=$reset"
echo "HCI_LOCAL_VERSION=$localver"

if [ "$probe_rc" -eq 0 ] && [ "$reset" = PASS ] && [ "$localver" = PASS ]; then
    echo "TRANSPORT_RESULT=PASS mode=$MODE boot_id=$BOOT_ID"
    exit 0
fi
echo "TRANSPORT_RESULT=FAIL mode=$MODE boot_id=$BOOT_ID probe_rc=$probe_rc reset=$reset local_version=$localver"
exit 1

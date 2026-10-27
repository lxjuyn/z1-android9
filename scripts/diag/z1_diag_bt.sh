#!/system/bin/sh
# ---------------------------------------------------------------------------
# z1_diag_bt.sh - Z1 Bluetooth deep-dive evidence probe
#
# Chain under test: mtk_btif_drv + mtk_stp_bt_soc (kernel) -> /dev/stpbt
#                   libbluetoothdrv.so <- libbt-vendor.so <- Bluetooth HIDL
#                   <- com.android.bluetooth
#
# Usage:
#   sh z1_diag_bt.sh            # passive only
#   sh z1_diag_bt.sh trigger    # also power BT on, wait, inspect, restore
#
# The trigger restores the previous bluetooth_on setting when it finishes.
# Writes only /data/local/z1diag/bt_<ts>/
# ---------------------------------------------------------------------------

PATH=/system/bin:/system/xbin:/vendor/bin:/sbin:$PATH
export PATH
umask 022

BASE=/data/local/z1diag
MODE=$1
TS=$(date +%Y%m%d_%H%M%S 2>/dev/null)
[ -n "$TS" ] || TS=$(cut -d. -f1 /proc/uptime 2>/dev/null)
OUT=$BASE/bt_$TS

have() { command -v "$1" >/dev/null 2>&1; }
say() {
    printf '[z1diag-bt] %s\n' "$*"
    printf '<6>[z1diag-bt] %s\n' "$*" > /dev/kmsg 2>/dev/null || true
}
cap() {
    _o=$1; _l=$2; _c=$3
    { printf '\n=== %s\n' "$_l"; sh -c "$_c" 2>&1; printf '=== end %s\n' "$_l"; } >> "$_o" 2>/dev/null || true
}
hdr() {
    : > "$2" 2>/dev/null || true
    { printf '# %s\n# %s\n' "$1" "$(date 2>/dev/null)"; } >> "$2" 2>/dev/null || true
}
fact() { printf '%s=%s\n' "$1" "$2" >> "$FACTS" 2>/dev/null || true; }
pc() { if [ -c "$1" ]; then printf 'yes'; else printf 'no'; fi; }
# count matching processes; the concurrent grep itself must not be counted
pcount() {
    _n=$( ( ps -A 2>/dev/null || ps 2>/dev/null ) | grep -- "$1" 2>/dev/null | grep -v 'grep' 2>/dev/null | wc -l )
    [ -n "$_n" ] || _n=0
    printf '%s' "$_n"
}

TO=
have timeout && TO="timeout 60"

mkdir -p "$OUT" 2>/dev/null || { echo "cannot create $OUT"; exit 1; }
FACTS=$OUT/facts.txt
: > "$FACTS" 2>/dev/null || true

say "start mode=${MODE:-passive} out=$OUT"

# ---------------------------------------------------------------------------
# 1. passive
# ---------------------------------------------------------------------------
CUR=$OUT/01_state.txt
hdr "Bluetooth state before any trigger" "$CUR"
cap "$CUR" "transport nodes" "ls -l /dev/stpbt /dev/stpwmt 2>/dev/null"
cap "$CUR" "nodes major:minor (expect stpbt 192:0)" \
    "ls -l /dev/stpbt; cat /proc/devices | grep -i -E 'stp|bt'"
cap "$CUR" "bt kernel modules" "grep -i -E 'stp_bt|btif|bluetooth|stp_uart|stp_wmt' /proc/modules"
cap "$CUR" "bt processes" "(ps -A 2>/dev/null || ps) | grep -i -E 'bluetooth|btif|hci' | grep -v grep"
cap "$CUR" "init service states" "getprop | grep -E 'init\.svc\.(bt|bluetooth)'"
cap "$CUR" "hciconfig -a" "hciconfig -a"
cap "$CUR" "rfkill" "rfkill list"
cap "$CUR" "/sys/class/bluetooth" "ls -l /sys/class/bluetooth"
cap "$CUR" "bt properties" "getprop | grep -i -E 'bluetooth|ro\.bt|persist\.bt|bdaddr'"
cap "$CUR" "vendor/HIDL libraries" \
    "ls -l /system/lib/libbluetoothdrv.so /system/lib/libbt-vendor.so /vendor/lib/libbt-vendor.so /system/lib/libstdc++.so /vendor/lib/libstdc++.so /vendor/lib/libc++.so 2>/dev/null"
cap "$CUR" "bluetooth HIDL service binary + rc" \
    "ls -l /vendor/bin/hw/android.hardware.bluetooth* /system/bin/hw/android.hardware.bluetooth* /vendor/etc/init/android.hardware.bluetooth* 2>/dev/null"
cap "$CUR" "dumpsys bluetooth_manager head" "$TO dumpsys bluetooth_manager | head -n 150"
cap "$CUR" "settings bluetooth_on / name" \
    "settings get global bluetooth_on; settings get secure bluetooth_name; settings get secure bluetooth_address"
cap "$CUR" "dmesg bt lines" \
    "dmesg 2>/dev/null | grep -i -E 'btif|stpbt|bluetooth|stp_bt|hci' | tail -n 300"

PRE_BT_ON=$(settings get global bluetooth_on 2>/dev/null)
fact PRE_STPBT_NODE "$(pc /dev/stpbt)"
fact PRE_BT_ON_SETTING "$PRE_BT_ON"
fact PRE_BT_STACK_PROCS "$(pcount com.android.bluetooth)"
fact PRE_HCI_DEV_NODES "$(ls /sys/class/bluetooth 2>/dev/null | wc -l)"

if [ "$MODE" != "trigger" ]; then
    say "passive mode done: $OUT"
    cat "$FACTS" 2>/dev/null
    exit 0
fi

# ---------------------------------------------------------------------------
# 2. trigger: power BT on
# ---------------------------------------------------------------------------
CUR=$OUT/02_trigger.txt
hdr "Bluetooth trigger: enable radio, observe stack" "$CUR"

if [ ! -c /dev/stpbt ]; then
    say "TRIGGER ABORT: /dev/stpbt missing -> no BT transport, stack cannot work."
    cap "$CUR" "why" "echo '/dev/stpbt absent: mtk_stp_bt/mtk_btif not loaded or node not created (init mknod fallback c 192 0)'; dmesg 2>/dev/null | grep -i -E 'stp_bt|btif|stpbt' | tail -n 40"
    fact TRIGGER_RESULT stpbt_missing
    exit 0
fi

say "enabling Bluetooth"
cap "$CUR" "enable attempt" \
    "svc bluetooth enable 2>&1 || cmd bluetooth_manager enable 2>&1 || echo 'no svc/cmd bluetooth control available'"

# wait for the stack to come up
i=0
seen=
while [ $i -lt 30 ]; do
    if [ "$(pcount com.android.bluetooth)" -ge 1 ]; then
        seen=$i
        break
    fi
    sleep 1
    i=$((i + 1))
done

cap "$CUR" "stack process appearance" "echo waited=${i}s found=${seen:-no}"
fact TRIGGER_STACK_APPEARED "$(pcount com.android.bluetooth)"
fact TRIGGER_STACK_WAIT_S "${seen:-none}"

sleep 3
cap "$CUR" "hciconfig after enable" "hciconfig -a; rfkill list"
cap "$CUR" "settings after enable" "settings get global bluetooth_on"
cap "$CUR" "dumpsys bluetooth_manager after enable" "$TO dumpsys bluetooth_manager | head -n 200"
cap "$CUR" "dmesg during enable" \
    "dmesg 2>/dev/null | grep -i -E 'btif|stpbt|bluetooth|hci|stp_bt' | tail -n 200"
cap "$CUR" "logcat during enable" \
    "logcat -d 2>/dev/null | grep -i -E 'bluetooth|btif|bt_vendor|hci|libbluetoothdrv' | tail -n 400"

if [ -n "$seen" ]; then
    fact TRIGGER_RESULT stack_started
else
    fact TRIGGER_RESULT stack_did_not_start
fi

# ---------------------------------------------------------------------------
# 3. restore previous radio state
# ---------------------------------------------------------------------------
if [ "$PRE_BT_ON" != "1" ]; then
    say "restoring bluetooth_on=$PRE_BT_ON (was off before the test)"
    svc bluetooth disable >/dev/null 2>&1 || cmd bluetooth_manager disable >/dev/null 2>&1 || true
    if [ -n "$PRE_BT_ON" ]; then
        settings put global bluetooth_on "$PRE_BT_ON" >/dev/null 2>&1 || true
    fi
fi

say "done: $OUT"
cat "$FACTS" 2>/dev/null
exit 0

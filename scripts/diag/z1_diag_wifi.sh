#!/system/bin/sh
# ---------------------------------------------------------------------------
# z1_diag_wifi.sh - Z1 WiFi (MT6620 SoC / wlan_gen2) deep-dive evidence probe
#
# Passive collection of the whole CONNSYS/WiFi chain, then an OPTIONAL trigger
# sequence that reproduces exactly what a normal boot does:
#     /dev/wmtWifi function-on  ->  wait for wlan0  ->  iw scan
#
# The trigger is opt-in:
#   sh z1_diag_wifi.sh            # passive only (safe, no state change)
#   sh z1_diag_wifi.sh trigger    # also do func-on + scan
#   sh z1_diag_wifi.sh trigger insmod
#                                 # additionally insmod the CONNSYS stack
#                                 # (only if /proc/modules has none of them)
#
# Writes only /data/local/z1diag/wifi_<ts>/   (never /system)
# POSIX sh, toybox/mksh compatible.
# ---------------------------------------------------------------------------

PATH=/system/bin:/system/xbin:/vendor/bin:/sbin:$PATH
export PATH
umask 022

BASE=/data/local/z1diag
MODE=$1
EXTRA=$2
TS=$(date +%Y%m%d_%H%M%S 2>/dev/null)
[ -n "$TS" ] || TS=$(cut -d. -f1 /proc/uptime 2>/dev/null)
OUT=$BASE/wifi_$TS

have() { command -v "$1" >/dev/null 2>&1; }
say() {
    printf '[z1diag-wifi] %s\n' "$*"
    printf '<6>[z1diag-wifi] %s\n' "$*" > /dev/kmsg 2>/dev/null || true
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
p() { if [ -e "$1" ]; then printf 'yes'; else printf 'no'; fi; }

TO=
have timeout && TO="timeout 60"

mkdir -p "$OUT" 2>/dev/null || { echo "cannot create $OUT"; exit 1; }
FACTS=$OUT/facts.txt
: > "$FACTS" 2>/dev/null || true

say "start mode=${MODE:-passive} out=$OUT"

# ---------------------------------------------------------------------------
# 1. passive evidence
# ---------------------------------------------------------------------------
CUR=$OUT/01_state.txt
hdr "WiFi state before any trigger" "$CUR"
cap "$CUR" "transport nodes" "ls -l /dev/stp* /dev/wmtWifi /dev/wmtcombo /dev/wmtdetect 2>/dev/null"
cap "$CUR" "modules" "grep -i -E 'wlan|wmt|stp|hif' /proc/modules"
cap "$CUR" "netdevs" "cat /proc/net/dev; ifconfig -a"
cap "$CUR" "iw dev" "iw dev"
cap "$CUR" "/sys/class/net" 'ls -l /sys/class/net; for d in /sys/class/net/*; do echo "-- $d"; cat "$d/operstate" 2>/dev/null; done'
cap "$CUR" "firmware tree" "ls -l /system/etc/firmware /vendor/etc/firmware 2>/dev/null"
cap "$CUR" "WiFi NVRAM blob (needed for calibration)" \
    "ls -l /system/etc/firmware/wifi-nvram-WIFI.bin /data/vendor/wifi 2>/dev/null"
cap "$CUR" "WMT cfg" "cat /system/etc/firmware/WMT_SOC.cfg 2>/dev/null"
cap "$CUR" "wmt debug node" "ls -l /proc/driver/wmt_dbg 2>/dev/null; head -n 60 /proc/driver/wmt_dbg 2>/dev/null"
cap "$CUR" "launcher / supplicant / dhcp processes" \
    "(ps -A 2>/dev/null || ps) | grep -i -E '6620|wmt|wpa_supplicant|dhcp' | grep -v grep"
cap "$CUR" "readiness properties (sys.wlan0.up is set by the v10 probe)" \
    "getprop sys.wlan0.up; getprop | grep -i -E 'wifi|wlan|wmt|connsys'"
cap "$CUR" "dumpsys wifi head" "$TO dumpsys wifi | head -n 120"
cap "$CUR" "dmesg wlan/wmt/stp lines" \
    "dmesg 2>/dev/null | grep -i -E 'wlan|wifi|wmt|stp|6620|hif|sdio' | tail -n 400"

fact PRE_WLAN0 "$(p /sys/class/net/wlan0)"
fact PRE_WMTWIFI_NODE "$(pc /dev/wmtWifi)"
fact PRE_MODULE_COUNT "$(grep -i -c -E 'wlan|wmt|stp' /proc/modules 2>/dev/null)"
fact PRE_WLAN0_UP_PROP "$(getprop sys.wlan0.up 2>/dev/null)"
fact PRE_WIFI_NVRAM "$(ls /system/etc/firmware/wifi-nvram-WIFI.bin 2>/dev/null | wc -l)"

# ---------------------------------------------------------------------------
# 2. optional insmod of the CONNSYS stack
# ---------------------------------------------------------------------------
if [ "$EXTRA" = "insmod" ]; then
    say "EXTRA=insmod requested; loading CONNSYS stack"
    MODDIR=/system/lib/modules
    for m in mtk_stp_uart mtk_stp_bt mtk_stp_gps mtk_wmt_wifi mtk_hif_sdio mtk_stp_wmt wlan_mt6620; do
        if [ -f "$MODDIR/$m.ko" ]; then
            if insmod "$MODDIR/$m.ko" >> "$OUT/02_insmod.txt" 2>&1; then
                say "insmod $m OK"
            else
                say "insmod $m FAILED (see 02_insmod.txt)"
            fi
        else
            say "missing $MODDIR/$m.ko"
        fi
    done
    sleep 3
    cap "$OUT/02_insmod.txt" "modules after insmod" "grep -i -E 'wlan|wmt|stp' /proc/modules"
    cap "$OUT/02_insmod.txt" "nodes after insmod" "ls -l /dev/stp* /dev/wmtWifi 2>/dev/null"
fi

# ---------------------------------------------------------------------------
# 3. trigger: WMT function-on -> wlan0 -> scan
# ---------------------------------------------------------------------------
if [ "$MODE" != "trigger" ]; then
    say "passive mode done: $OUT"
    cat "$FACTS" 2>/dev/null
    exit 0
fi

CUR=$OUT/03_trigger.txt
hdr "WiFi trigger: func-on, wlan0 wait, scan" "$CUR"

if [ ! -c /dev/wmtWifi ]; then
    say "TRIGGER ABORT: /dev/wmtWifi missing (not a char device)."
    cap "$CUR" "why" "echo '/dev/wmtWifi absent: WMT platform driver did not create it (init mknod fallback creates c 153 0 only if missing)'; ls -l /dev/wmtWifi 2>/dev/null; dmesg 2>/dev/null | grep -i -E 'wmtWifi|wmt' | tail -n 40"
    fact TRIGGER_RESULT wmtwifi_node_missing
    exit 0
fi

say "func-on: echo 1 > /dev/wmtWifi"
echo 1 > /dev/wmtWifi 2>>"$CUR" || say "func-on write failed (see 03_trigger.txt)"

found=
i=0
while [ $i -lt 40 ]; do
    if [ -d /sys/class/net/wlan0 ]; then
        found=$i
        break
    fi
    sleep 1
    i=$((i + 1))
done

cap "$CUR" "wlan0 appearance" "echo waited=${i}s found=${found:-no}"
fact TRIGGER_WLAN0_APPEARED "$(p /sys/class/net/wlan0)"
fact TRIGGER_WLAN0_WAIT_S "${found:-none}"

if [ -z "$found" ]; then
    say "TRIGGER FAIL: wlan0 never appeared within 40s."
    cap "$CUR" "diagnosis help" \
        "dmesg 2>/dev/null | grep -i -E 'wlan|wmt|hif|sdio|6620|firmware' | tail -n 200; echo ---; grep -i -E 'wlan|wmt|stp' /proc/modules"
    fact TRIGGER_RESULT wlan0_missing
    exit 0
fi

say "wlan0 present after ${found}s; bringing up and scanning"

# IMPORTANT (learned from the v10 probe audit): "wlan0 exists" is NOT
# readiness. The netdev shows up at insmod time, before the firmware/NVRAM
# download finishes, so an immediate scan always fails on a healthy system.
# Hence: bring it up, then wait for carrier, then retry the scan.
cap "$CUR" "wlan0 bring-up" "ifconfig wlan0 up; ip link set wlan0 up; sleep 1; ifconfig wlan0; iw dev wlan0 info"

carrier=
k=0
while [ $k -lt 15 ]; do
    c=$(cat /sys/class/net/wlan0/carrier 2>/dev/null)
    op=$(cat /sys/class/net/wlan0/operstate 2>/dev/null)
    if [ "$c" = "1" ] || [ "$op" = "up" ]; then
        carrier=$k
        break
    fi
    sleep 1
    k=$((k + 1))
done
cap "$CUR" "carrier/operstate wait" \
    "echo waited=${k}s carrier=${carrier:-no}; cat /sys/class/net/wlan0/carrier /sys/class/net/wlan0/operstate 2>/dev/null"
cap "$CUR" "firmware / nvram evidence" \
    "ls -l /system/etc/firmware/wifi-nvram-WIFI.bin /data/vendor/wifi 2>/dev/null; dmesg 2>/dev/null | grep -i -E 'firmware|nvram|WIFI_RAM_CODE' | tail -n 40"
fact TRIGGER_WLAN0_CARRIER "${carrier:-no}"
fact TRIGGER_WLAN0_UP_PROP "$(getprop sys.wlan0.up 2>/dev/null)"

# Scan with retries: up to 3 passes with a settle gap, first non-empty wins.
SCAN=$OUT/04_scan_raw.txt
: > "$SCAN" 2>/dev/null || true
NSSID=0
attempt=0
while [ $attempt -lt 3 ] && [ "$NSSID" -eq 0 ]; do
    attempt=$((attempt + 1))
    if [ $attempt -gt 1 ]; then
        say "scan attempt ${attempt}: settling 8s first"
        sleep 8
        ifconfig wlan0 up 2>/dev/null || true
    fi
    if have timeout; then
        timeout 90 iw dev wlan0 scan > "$SCAN" 2>&1 || true
    else
        iw dev wlan0 scan > "$SCAN" 2>&1 || true
    fi
    NSSID=$(grep -c '^[[:space:]]*SSID:' "$SCAN" 2>/dev/null)
    [ -n "$NSSID" ] || NSSID=0
    say "scan attempt ${attempt}: ${NSSID} SSID lines"
done

cap "$CUR" "scan summary (from 04_scan_raw.txt)" \
    "grep -E 'SSID|frequency:|signal:' $SCAN | head -n 80"

say "scan produced $NSSID SSID lines after ${attempt} attempt(s)"
fact TRIGGER_SCAN_ATTEMPTS "$attempt"
fact TRIGGER_SCAN_SSIDS "$NSSID"
if [ "$NSSID" -gt 0 ]; then
    fact TRIGGER_RESULT scan_ok
elif [ -n "$carrier" ]; then
    fact TRIGGER_RESULT scan_empty_but_carrier_up
else
    fact TRIGGER_RESULT scan_empty_no_carrier
fi

cap "$CUR" "iw link / station dump" "iw dev wlan0 link; iw dev wlan0 station dump"
cap "$CUR" "dmesg during scan" "dmesg 2>/dev/null | grep -i -E 'wlan|wmt|scan|6620' | tail -n 120"
cap "$CUR" "logcat wifi during trigger" \
    "logcat -d 2>/dev/null | grep -i -E 'wlan|wifi|wpa|wmt|supplicant' | tail -n 200"

say "done: $OUT (func-on left ACTIVE; reboot to clear)"
cat "$FACTS" 2>/dev/null
exit 0

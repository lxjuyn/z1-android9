#!/system/bin/sh
# ---------------------------------------------------------------------------
# z1_diag_gps.sh - Z1 GNSS/GPS deep-dive evidence probe
#
# Chain under test: /dev/stpgps (mtk_stp_gps) -> mnld (MTK daemon, needs
#                   libnvram + factory NVRAM) -> gps HAL -> framework Location.
#
# Usage:
#   sh z1_diag_gps.sh            # passive only
#   sh z1_diag_gps.sh trigger    # enable location, watch the stack try a fix,
#                                # then restore the previous location_mode
#
# Writes only /data/local/z1diag/gps_<ts>/
# ---------------------------------------------------------------------------

PATH=/system/bin:/system/xbin:/vendor/bin:/sbin:$PATH
export PATH
umask 022

BASE=/data/local/z1diag
MODE=$1
TS=$(date +%Y%m%d_%H%M%S 2>/dev/null)
[ -n "$TS" ] || TS=$(cut -d. -f1 /proc/uptime 2>/dev/null)
OUT=$BASE/gps_$TS

have() { command -v "$1" >/dev/null 2>&1; }
say() {
    printf '[z1diag-gps] %s\n' "$*"
    printf '<6>[z1diag-gps] %s\n' "$*" > /dev/kmsg 2>/dev/null || true
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
hdr "GNSS state before any trigger" "$CUR"
cap "$CUR" "gps device nodes" \
    "ls -l /dev/stpgps /dev/gps /dev/gps1 /dev/gps2 /dev/ttyMT1 2>/dev/null"
cap "$CUR" "gps modules" "grep -i -E 'stp_gps|gps|stp_uart' /proc/modules"
cap "$CUR" "gps processes (mnld is the MTK daemon)" \
    "(ps -A 2>/dev/null || ps) | grep -i -E 'mnld|gps|gnss' | grep -v grep"
cap "$CUR" "GPS daemon binary / configs" \
    "ls -l /system/bin/mnld /vendor/bin/mnld /system/etc/mnl* /vendor/etc/mnl* /system/etc/gps* /vendor/etc/gps* 2>/dev/null"
cap "$CUR" "GPS HAL binaries" \
    "ls -l /vendor/bin/hw/android.hardware.gnss* /system/bin/hw/android.hardware.gnss* /vendor/lib/hw/gps*.so /system/lib/hw/gps*.so 2>/dev/null"
cap "$CUR" "gnss properties" "getprop | grep -i -E 'gps|gnss|mnl'"
cap "$CUR" "nvram libraries (mnld needs them)" \
    "ls -l /system/lib/libnvram*.so /vendor/lib/libnvram*.so 2>/dev/null"
cap "$CUR" "factory NVRAM / GPS data dirs" \
    "ls -ld /data/misc/GPS* /data/misc/gps /data/vendor/gps /data/gps /data/misc/nvram 2>/dev/null"
cap "$CUR" "factory NVRAM / GPS data contents (capped)" \
    "ls -lR /data/misc/GPS* /data/vendor/gps /data/misc/nvram 2>/dev/null | head -n 150"
cap "$CUR" "location settings" \
    "settings get secure location_mode; settings get secure location_providers_allowed; settings get secure location_providers_allowed -u 0 2>/dev/null"
cap "$CUR" "dumpsys location head" "$TO dumpsys location | head -n 150"
cap "$CUR" "dmesg gps lines" \
    "dmesg 2>/dev/null | grep -i -E 'gps|gnss|stp_gps' | tail -n 200"

PRE_LOCMODE=$(settings get secure location_mode 2>/dev/null)
fact PRE_STPGPS_NODE "$(pc /dev/stpgps)"
fact PRE_MNLD_PROCS "$(pcount mnld)"
fact PRE_LOCATION_MODE "$PRE_LOCMODE"
fact GPS_HAL_BINARIES "$(ls /vendor/bin/hw/android.hardware.gnss* /system/bin/hw/android.hardware.gnss* 2>/dev/null | wc -l)"
fact GPS_HAL_LIBS "$(ls /vendor/lib/hw/gps*.so /system/lib/hw/gps*.so 2>/dev/null | wc -l)"
fact GPS_MNLD_BINARY "$(ls /system/bin/mnld /vendor/bin/mnld 2>/dev/null | wc -l)"

if [ "$MODE" != "trigger" ]; then
    say "passive mode done: $OUT"
    cat "$FACTS" 2>/dev/null
    exit 0
fi

# ---------------------------------------------------------------------------
# 2. trigger: enable location and watch for a fix attempt
# ---------------------------------------------------------------------------
CUR=$OUT/02_trigger.txt
hdr "GNSS trigger: location on, watch 60s for engineers" "$CUR"

if [ "$(pcount mnld)" -eq 0 ] && [ "$(ls /system/bin/mnld /vendor/bin/mnld 2>/dev/null | wc -l)" -eq 0 ]; then
    say "TRIGGER NOTE: no mnld binary and no mnld process -> GPS userspace is NOT deployed on this build."
    fact TRIGGER_RESULT gps_userspace_absent
fi

say "setting location_mode=3"
cap "$CUR" "enable location" \
    "settings put secure location_mode 3; settings put secure location_providers_allowed +gps,+network 2>/dev/null; settings get secure location_mode"

# sample the stack for a while: what starts, what it says
i=0
while [ $i -lt 60 ]; do
    sleep 10
    i=$((i + 10))
    echo "--- t+${i}s mnld=$(pcount mnld) stpgps=$(pc /dev/stpgps)" >> "$CUR" 2>/dev/null || true
done

cap "$CUR" "processes after enable" \
    "(ps -A 2>/dev/null || ps) | grep -i -E 'mnld|gps|gnss' | grep -v grep"
cap "$CUR" "dumpsys location after enable" "$TO dumpsys location | head -n 200"
cap "$CUR" "dmesg after enable" \
    "dmesg 2>/dev/null | grep -i -E 'gps|gnss|stp_gps|nvram' | tail -n 200"
cap "$CUR" "logcat gnss/gps after enable" \
    "logcat -d 2>/dev/null | grep -i -E 'gnss|gps|mnld|libgps|location' | tail -n 500"
cap "$CUR" "avc denials while gnss starts" \
    "dmesg 2>/dev/null | grep -i 'avc:' | grep -i -E 'gps|mnl|nvram' | tail -n 60"

if [ "$(pcount mnld)" -ge 1 ]; then
    fact TRIGGER_RESULT mnld_running
else
    fact TRIGGER_RESULT mnld_absent
fi

# restore
if [ -n "$PRE_LOCMODE" ] && [ "$PRE_LOCMODE" != "3" ]; then
    say "restoring location_mode=$PRE_LOCMODE"
    settings put secure location_mode "$PRE_LOCMODE" >/dev/null 2>&1 || true
fi

say "done: $OUT"
cat "$FACTS" 2>/dev/null
exit 0

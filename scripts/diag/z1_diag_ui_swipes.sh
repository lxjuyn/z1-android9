#!/system/bin/sh
# Run from an unlocked Z1 shell with the chosen launcher or scrollable app open.
# Usage: sh z1_diag_ui_swipes.sh PACKAGE [RUN_LABEL]
set -u

fail() { printf 'INVALID: %s\n' "$*" >&2; exit 1; }
pkg=${1:-}
label=${2:-baseline}
case "$pkg" in ''|*[!a-zA-Z0-9_.]*) fail 'provide an Android package name';; esac
case "$label" in ''|*[!a-zA-Z0-9_.-]*) fail 'invalid run label';; esac

proc_identity() {
    proc_pid=$(pidof "$1") || return 1
    case "$proc_pid" in ''|*[!0-9]*) return 1;; esac
    # These process names have no spaces in comm. Field 22 is starttime.
    proc_start=$(awk '$22 > 0 { print $22 }' "/proc/$proc_pid/stat" 2>/dev/null) || return 1
    [ -n "$proc_start" ] || return 1
    printf '%s:%s\n' "$proc_pid" "$proc_start"
}

check_input() {
    [ "$(getprop sys.boot_completed)" = 1 ] || fail 'Android has not completed boot'
    [ "$(service check input)" = 'Service input: found' ] || fail 'Android input service unavailable'
}

check_foreground() {
    focus=$(dumpsys -t 10 window windows | awk '/mCurrentFocus=/{print}')
    case "$focus" in *" $pkg/"*) ;; *) fail "unlock and open $pkg before the run";; esac
}

check_input
check_foreground
server=$(proc_identity system_server) || fail 'cannot identify system_server'
app_pid=$(pidof "$pkg") || fail 'application has no process'
size=$(wm size | awk '/^(Physical|Override) size:/{s=$NF} END {print s}')
width=${size%x*}
height=${size#*x}
case "$width:$height" in *[!0-9:]*|:|:*|*:) fail 'cannot read display size';; esac
[ "$width" -ge 120 ] && [ "$height" -ge 160 ] || fail 'display size is invalid'
x=$((width / 2))
lo=$((height * 3 / 10))
hi=$((height * 7 / 10))

printf 'Z1_UI_BEGIN label=%s package=%s size=%s swipes=15 duration_ms=300 system_server=%s\n' "$label" "$pkg" "$size" "$server"
for p in /sys/devices/system/cpu/cpufreq/policy0/scaling_governor \
         /sys/devices/system/cpu/cpufreq/policy0/scaling_min_freq \
         /sys/devices/system/cpu/cpufreq/policy0/scaling_max_freq \
         /sys/class/devfreq/13010000.gpu/governor; do
    if [ -r "$p" ]; then printf '%s=%s\n' "$p" "$(cat "$p")"; fi
done
printf 'hwcomposer=%s\n' "$(getprop z1stage.hwcomposer.sha)"
dumpsys -t 10 gfxinfo "$pkg" reset >/dev/null || fail 'cannot reset app frame counters'
i=0
while [ "$i" -lt 15 ]; do
    if [ $((i % 2)) -eq 0 ]; then
        input touchscreen swipe "$x" "$hi" "$x" "$lo" 300 || fail 'input swipe failed'
    else
        input touchscreen swipe "$x" "$lo" "$x" "$hi" 300 || fail 'input swipe failed'
    fi
    sleep 1
    i=$((i + 1))
done
check_input
[ "$(proc_identity system_server)" = "$server" ] || fail 'system_server restarted during run'
[ "$(pidof "$pkg")" = "$app_pid" ] || fail 'application restarted during run'
check_foreground
stats=$(dumpsys -t 10 gfxinfo "$pkg") || fail 'cannot read app frame counters'
printf '%s\n' "$stats"
frames=$(printf '%s\n' "$stats" | awk '/Total frames rendered:/{n+=$NF} END {print n+0}')
[ "$frames" -gt 0 ] || fail 'no frames measured; choose a screen that scrolls'
printf 'Z1_UI_END valid=1 frames=%s\n' "$frames"

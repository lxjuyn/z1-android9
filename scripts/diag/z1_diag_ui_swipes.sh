#!/system/bin/sh
# Run from an unlocked Z1 shell with the chosen launcher or scrollable app open.
# Usage: sh z1_diag_ui_swipes.sh PACKAGE [RUN_LABEL]
set -u

fail() { printf 'Z1_UI_END valid=0 reason=%s\n' "$*" >&2; exit 1; }
clock() { awk '{print $1}' /proc/uptime; }
boot_identity() { cat /proc/sys/kernel/random/boot_id; }
pkg=${1:-}
label=${2:-baseline}
case "$pkg" in ''|*[!a-zA-Z0-9_.]*) fail 'provide an Android package name';; esac
case "$label" in ''|*[!a-zA-Z0-9_.-]*) fail 'invalid run label';; esac

proc_identity() {
    proc_pid=$(pidof "$1") || return 1
    case "$proc_pid" in ''|*[!0-9]*) return 1;; esac
    # Remove the parenthesized comm; starttime is field 20 of the rest.
    proc_start=$(awk '{sub(/^.*\) /, ""); if ($20 > 0) print $20}' "/proc/$proc_pid/stat" 2>/dev/null) || return 1
    [ -n "$proc_start" ] || return 1
    printf '%s:%s\n' "$proc_pid" "$proc_start"
}

check_input() {
    [ "$(getprop sys.boot_completed)" = 1 ] || fail 'Android has not completed boot'
    [ "$(timeout 8 service check input)" = 'Service input: found' ] || fail 'Android input service unavailable'
}

dump_state() {
    state=$(timeout 12 dumpsys -t 10 "$@" 2>&1) || return 1
    # Pie dumpsys can report a per-service timeout but still exit zero.
    case "$state" in ''|*'DUMP TIMEOUT'*) return 1;; esac
    printf '%s\n' "$state"
}

check_foreground() {
    windows=$(dump_state window windows) || fail 'window focus dump failed'
    focus=$(printf '%s\n' "$windows" | awk '/mCurrentFocus=/{print}')
    case "$focus" in *" $pkg/"*) ;; *) fail "unlock and open $pkg before the run";; esac
    policy=$(dump_state window policy) || fail 'window policy dump failed'
    printf '%s\n' "$policy" | awk '
        $1 == "KeyguardServiceDelegate" { delegate=1; next }
        $1 == "KeyguardStateMonitor" { delegate=0 }
        delegate && $1 == "showing=false" { hidden=1 }
        delegate && $1 == "inputRestricted=false" { unrestricted=1 }
        /isStatusBarKeyguard=true/ { statusbar=1 }
        END { exit !(hidden && unrestricted && !statusbar) }
    ' || fail 'cannot confirm unlocked keyguard policy'
    power=$(dump_state power) || fail 'power state dump failed'
    printf '%s\n' "$power" | awk '
        $1 == "mWakefulness=Awake" { awake=1 }
        $0 ~ /Display Power: state=ON[[:space:]]*$/ { on=1 }
        END { exit !(awake && on) }
    ' || fail 'cannot confirm awake display'
}

snapshot() {
    printf 'Z1_UI_DISK phase=%s uptime=%s fields=proc_diskstats\n' "$1" "$(clock)"
    awk '$3 == "mmcblk0" { print "Z1_UI_DISKSTAT " $0 }' /proc/diskstats
}

check_input
check_foreground
boot=$(boot_identity) || fail 'cannot identify boot'
[ -n "$boot" ] || fail 'empty boot identity'
server=$(proc_identity system_server) || fail 'cannot identify system_server'
app=$(proc_identity "$pkg") || fail 'cannot identify application process'
composer=$(proc_identity surfaceflinger) || fail 'cannot identify SurfaceFlinger'
size=$(timeout 8 wm size | awk '/^(Physical|Override) size:/{s=$NF} END {print s}')
width=${size%x*}
height=${size#*x}
case "$width:$height" in *[!0-9:]*|:|:*|*:) fail 'cannot read display size';; esac
[ "$width" -ge 120 ] && [ "$height" -ge 160 ] || fail 'display size is invalid'
x=$((width / 2))
lo=$((height * 3 / 10))
hi=$((height * 7 / 10))

printf 'Z1_UI_BEGIN label=%s package=%s size=%s swipes=15 duration_ms=300 boot=%s system_server=%s app=%s surfaceflinger=%s\n' \
    "$label" "$pkg" "$size" "$boot" "$server" "$app" "$composer"
for p in /sys/devices/system/cpu/cpufreq/policy0/scaling_governor \
         /sys/devices/system/cpu/cpufreq/policy0/scaling_min_freq \
         /sys/devices/system/cpu/cpufreq/policy0/scaling_max_freq \
         /sys/class/devfreq/13010000.gpu/governor; do
    if [ -r "$p" ]; then printf '%s=%s\n' "$p" "$(cat "$p")"; fi
done
printf 'hwcomposer=%s\n' "$(getprop z1stage.hwcomposer.sha)"
dump_state gfxinfo "$pkg" reset >/dev/null || fail 'cannot reset app frame counters'
snapshot before
i=0
while [ "$i" -lt 15 ]; do
    printf 'Z1_UI_SWIPE index=%s phase=begin uptime=%s\n' "$i" "$(clock)"
    if [ $((i % 2)) -eq 0 ]; then
        timeout 8 input touchscreen swipe "$x" "$hi" "$x" "$lo" 300 || fail 'input swipe failed'
    else
        timeout 8 input touchscreen swipe "$x" "$lo" "$x" "$hi" 300 || fail 'input swipe failed'
    fi
    printf 'Z1_UI_SWIPE index=%s phase=end uptime=%s\n' "$i" "$(clock)"
    sleep 1
    i=$((i + 1))
done
snapshot after
check_input
[ "$(boot_identity)" = "$boot" ] || fail 'boot changed during run'
[ "$(proc_identity system_server)" = "$server" ] || fail 'system_server restarted during run'
[ "$(proc_identity "$pkg")" = "$app" ] || fail 'application restarted during run'
[ "$(proc_identity surfaceflinger)" = "$composer" ] || fail 'SurfaceFlinger restarted during run'
check_foreground
stats=$(dump_state gfxinfo "$pkg" framestats) || fail 'cannot read app frame counters'
printf '%s\n' "$stats"
frames=$(printf '%s\n' "$stats" | awk '/Total frames rendered:/{n+=$NF} END {print n+0}')
[ "$frames" -gt 0 ] || fail 'no frames measured; choose a screen that scrolls'
# A lifetime counter alone is not a frame-time sample. Require completed,
# unflagged rows in the Android 9 PROFILEDATA block as well.
samples=$(printf '%s\n' "$stats" | awk -F, '
    /^---PROFILEDATA---/ { in_profile=!in_profile; next }
    in_profile && $1 == "Flags" {
        start=0; end=0
        for(i=1;i<=NF;i++) {
            if($i=="IntendedVsync") start=i
            if($i=="FrameCompleted") end=i
        }
        next
    }
    in_profile && start && end && $1=="0" && $start~/^[0-9]+$/ &&
        $end~/^[0-9]+$/ && $start>0 && $end>$start && ($end-$start)<1e12 { n++ }
    END { print n+0 }
')
[ "$samples" -gt 0 ] || fail 'no completed frame timestamps'
[ "$(boot_identity)" = "$boot" ] && [ "$(proc_identity system_server)" = "$server" ] &&
    [ "$(proc_identity "$pkg")" = "$app" ] && [ "$(proc_identity surfaceflinger)" = "$composer" ] ||
    fail 'runtime changed during frame dump'
printf 'Z1_UI_END valid=1 frames=%s timestamp_samples=%s boot=%s\n' "$frames" "$samples" "$boot"

#!/system/bin/sh
# Run from an unlocked Z1 shell with the chosen launcher or scrollable app open.
# Usage: sh z1_diag_ui_swipes.sh PACKAGE [RUN_LABEL] [EXACT_SF_LAYER]
set -u

fail() { printf 'Z1_UI_END valid=0 reason=%s\n' "$*" >&2; exit 1; }
clock() { awk '{print $1}' /proc/uptime; }
boot_identity() { cat /proc/sys/kernel/random/boot_id; }
pkg=${1:-}
label=${2:-baseline}
layer=${3:-}
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

# These are lifetime HWC counters. Only subtract snapshots from the same
# instance, and preserve a successful app sample if the HAL is busy or older.
hwc_snapshot() {
    dump_state SurfaceFlinger | awk '
        $1 == "z1hwc2_metrics" { line=$0; count++ }
        END {
            if (count == 1) print line
            else if (count == 0) print "z1hwc2_metrics unavailable=missing"
            else print "z1hwc2_metrics unavailable=ambiguous"
        }
    '
}

hwc_instance() {
    printf '%s\n' "$1" | awk '{
        for (i=2; i<=NF; i++) {
            if ($i ~ /^version=.+$/) version=$i
            if ($i ~ /^opened_ms=[0-9]+$/) opened=$i
        }
        if (version && opened) print version, opened
    }'
}

# Pie --latency reports desired-present, actual-present and frame-ready in ns.
# Subtract the pre-swipe ring by desired timestamp, including pending rows.
layer_rows() {
    awk -v swipe="$1" '
        $0 == "AFTER" { after=1; next }
        NF==1 && $1~/^[0-9]+$/ && $1+0>0 {
            if(after) period=$1; else baseline_period=$1
            next
        }
        NF!=3 || $1!~/^[0-9]+$/ || $1+0<=0 { next }
        !after { old["t"$1]=1; next }
        ("t"$1) in old { next }
        $2~/^[0-9]+$/ && $3~/^[0-9]+$/ && $2+0>0 && $3+0>0 &&
            $2+0<9e18 && $3+0<9e18 {
            print "Z1_UI_LAYER_FRAME", "index="swipe, "desired_ns="$1, "present_ns="$2, "ready_ns="$3
            n++
        }
        END {
            if(!baseline_period || !period || period!=baseline_period) exit 1
            print "Z1_UI_LAYER_SWIPE", "index="swipe, "refresh_ns="period, "samples="n+0
        }
    '
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

if [ -n "$layer" ]; then
    layers=$(dump_state SurfaceFlinger --list) || fail 'cannot list SurfaceFlinger layers'
    printf '%s\n' "$layers" | grep -Fxq -- "$layer" || fail 'exact layer unavailable'
    case "$layer" in "$pkg/"*) ;; *" $pkg/"*) ;; *) fail 'layer does not belong to package';; esac
    printf 'Z1_UI_LAYER name=%s\n' "$layer"
    layer_total=0
fi
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
hwc_before=$(hwc_snapshot)
printf 'Z1_UI_HWC phase=before uptime=%s %s\n' "$(clock)" "$hwc_before"
i=0
while [ "$i" -lt 15 ]; do
    if [ -n "$layer" ]; then
        prior=$(dump_state SurfaceFlinger --latency "$layer") || fail 'layer baseline unavailable'
    fi
    printf 'Z1_UI_SWIPE index=%s phase=begin uptime=%s\n' "$i" "$(clock)"
    if [ $((i % 2)) -eq 0 ]; then
        timeout 8 input touchscreen swipe "$x" "$hi" "$x" "$lo" 300 || fail 'input swipe failed'
    else
        timeout 8 input touchscreen swipe "$x" "$lo" "$x" "$hi" 300 || fail 'input swipe failed'
    fi
    printf 'Z1_UI_SWIPE index=%s phase=end uptime=%s\n' "$i" "$(clock)"
    sleep 1
    if [ -n "$layer" ]; then
        current=$(dump_state SurfaceFlinger --latency "$layer") || fail 'layer sample unavailable'
        rows=$(printf '%s\nAFTER\n%s\n' "$prior" "$current" | layer_rows "$i") || fail 'invalid layer latency response'
        printf '%s\n' "$rows"
        count=${rows##*samples=}
        layer_total=$((layer_total + count))
    fi
    i=$((i + 1))
done
if [ -n "$layer" ]; then
    [ "$layer_total" -gt 0 ] || fail 'no new completed frames on selected layer'
fi
hwc_after=$(hwc_snapshot)
printf 'Z1_UI_HWC phase=after uptime=%s %s\n' "$(clock)" "$hwc_after"
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
hwc_before_instance=$(hwc_instance "$hwc_before")
hwc_after_instance=$(hwc_instance "$hwc_after")
if [ -z "$hwc_before_instance" ] || [ -z "$hwc_after_instance" ]; then
    printf 'Z1_UI_HWC_PAIR valid=0 reason=snapshot_unavailable\n'
elif [ "$hwc_before_instance" != "$hwc_after_instance" ]; then
    printf 'Z1_UI_HWC_PAIR valid=0 reason=instance_changed\n'
else
    printf 'Z1_UI_HWC_PAIR valid=1\n'
fi
printf 'Z1_UI_END valid=1 frames=%s timestamp_samples=%s boot=%s\n' "$frames" "$samples" "$boot"

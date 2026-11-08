#!/system/bin/sh
# z1_gpu_watch.sh — decide "the GPU stack is broken" without a display, and arm
# the gate for the next boot.
#
# INVOCATIONS
#   z1_gpu_watch.sh                 daemon (started once at post-fs-data)
#   z1_gpu_watch.sh event <state>   one-shot, fed by
#                                   on property:init.svc.surfaceflinger=*
#                                   ("restarting" is one of this init's four
#                                    service state strings: strings init |
#                                    grep -x 'stopped\|starting\|running\|restarting')
#
# SIGNALS (a rainbow screen does NOT restart anything, so both are needed):
#   1. surfacesflinger restarts (init.svc.surfaceflinger -> stopped/restarting,
#      or the pid changes)      >= 3 per boot
#   2. lima GP errors in the kernel ring buffer, the exact string this driver
#      prints (fork_linux/drivers/gpu/drm/lima/lima_gp.c:40):
#         "lima 13010000.gpu: gp error irq state=4001 status=1"
#      observed 20x on the board with Mesa 19.1.8
#      (mainline_recon/z1_android9_acm_20261020_190823_409363.log)
#      trip when the count grows by >= 5 within this boot.
#      CONFIG_SECURITY_DMESG_RESTRICT is not set (fork_linux/.config:6645).
#
# On trip: writes the disable marker to /data/local/tmp (shell-writable, so the
# console shell can undo it), /data and /cache (survives a broken /data), sets
# persist.sys.z1.gpu.disabled=1, and optionally reboots so the next boot comes
# up on SwiftShader. Reboot is opt-in:  setprop persist.sys.z1.gpu.autoreboot 1
# (default off — a spurious trip should not turn into a reboot loop on a device
# whose only console is a serial line).

# init's exec/exec_background do not guarantee a usable PATH; the scripts call
# toybox applets (mount cp md5sum wc cut pidof dmesg grep sleep). Appending is
# harmless on a host used for dry runs.
case ":$PATH:" in
    *:/system/bin:*) : ;;
    *) PATH="$PATH:/system/bin:/vendor/bin:/sbin" ;;
esac
export PATH

MARK_DIR=${Z1_MARK_DIR:-/data/local/tmp}
CACHE_DIR=${Z1_CACHE_DIR:-/cache}
EGL_DIR=${Z1_EGL_DIR:-/vendor/lib/egl}
MESA="$EGL_DIR/libGLES_mesa.so"

K_RESTARTS=3
K_LIMA_ERR=5
POLL=5
MAX_POLLS=240        # 20 minutes

klog()  { [ -w /dev/kmsg ] && printf '<6>[z1gpu] watch: %s\n' "$*" > /dev/kmsg 2>/dev/null; return 0; }
kwarn() { [ -w /dev/kmsg ] && printf '<4>[z1gpu] watch: %s\n' "$*" > /dev/kmsg 2>/dev/null; return 0; }
prop()  { command -v getprop >/dev/null 2>&1 && getprop "$1" 2>/dev/null; return 0; }
bootid() { cat /proc/sys/kernel/random/boot_id 2>/dev/null; }

STATE="$MARK_DIR/.z1_gpu_watch.state"   # "<boot_id> <restarts>"
BOOTID=$(bootid)

restarts() {
    set -- $(cat "$STATE" 2>/dev/null)
    [ "$1" = "$BOOTID" ] && echo "${2:-0}" || echo 0
}
set_restarts() {
    printf '%s %s\n' "$BOOTID" "$1" > "$STATE" 2>/dev/null
}

# v33: 标记只作为取证信息，不再让 daemon 提前退出（否则 init 会每 5 秒把它重启一次）。
tripped() { return 1; }
marker_state() { [ -e "$MARK_DIR/z1_gpu_disable" ] && echo "data-marker" ; return 0; }

# NOTE: `: > file` on a missing/read-only path is FATAL in a POSIX shell (":"
# is a special builtin), so every marker write goes through a subshell. This is
# exactly the case /cache is meant to cover: if /data is read-only the failure
# must not stop us from writing the /cache marker.
write_marker() { ( : > "$1" ) 2>/dev/null || true; }

trip() {
    why="$1"
    tripped && { klog "already tripped ($why)"; return 0; }
    kwarn "TRIP: $why"
    # 2026-11-08 (v33): 只记录，不再写 disable 标记、不再 autoreboot。
    # 原因：disable 标记会让 z1_gpu_gate.sh 藏掉 mesa，而 SwiftShader 回退在本镜像里
    # dlopen 不进去 -> SurfaceFlinger 崩 -> 无限重启（实测已复现）。GPU 出问题时我们
    # 宁可在屏幕上看到坏画面，也不要让设备进不了系统。
    kwarn "v33: trip is LOG-ONLY (no markers, no reboot); gate keeps mesa enabled"
    sync 2>/dev/null
    exit 0
    mkdir -p "$MARK_DIR" 2>/dev/null
    write_marker "$MARK_DIR/z1_gpu_disable"
    write_marker "/data/z1_gpu_disable"
    write_marker "$CACHE_DIR/z1_gpu_disable"
    command -v setprop >/dev/null 2>&1 && {
        setprop persist.sys.z1.gpu.disabled 1 2>/dev/null
        setprop sys.z1.gpu.tripped 1 2>/dev/null
    }
    klog "marker written; mesa will be hidden on the next boot by z1_gpu_gate.sh"
    sync 2>/dev/null
    if [ "$(prop persist.sys.z1.gpu.autoreboot)" = "1" ] \
       && [ ! -e "$MARK_DIR/.z1_gpu_autorebooted" ]; then
        write_marker "$MARK_DIR/.z1_gpu_autorebooted"
        kwarn "autoreboot=1 -> rebooting in 5s"
        sleep 5
        sync 2>/dev/null
        command -v reboot >/dev/null 2>&1 && reboot 2>/dev/null
    fi
    exit 0
}

lima_errs() {
    command -v dmesg >/dev/null 2>&1 || { echo 0; return; }
    dmesg 2>/dev/null | grep -c "gp error irq" 2>/dev/null || echo 0
}

# ---------------------------------------------------------------- event mode
if [ "$1" = "event" ]; then
    tripped && exit 0
    [ -e "$MESA" ] || exit 0            # gate already active: nothing to watch
    state="$2"
    case "$state" in
        stopped|restarting)
            n=$(restarts); n=$((n + 1)); set_restarts "$n"
            klog "surfaceflinger -> $state (restart $n/$K_RESTARTS)"
            [ "$n" -ge "$K_RESTARTS" ] && trip "surfaceflinger restarted ${n}x"
            ;;
        *) : ;;
    esac
    exit 0
fi

# ---------------------------------------------------------------- daemon mode
klog "daemon start (boot=$BOOTID, mesa=$([ -e "$MESA" ] && echo present || echo absent))"
[ -e "$MESA" ] || { klog "mesa absent -> nothing to watch"; exit 0; }
tripped && { klog "disable marker already present -> exiting"; exit 0; }

base=$(lima_errs)
klog "lima error baseline=$base"
lastpid=$(pidof surfaceflinger 2>/dev/null)
i=0
while [ "$i" -lt "$MAX_POLLS" ]; do
    sleep "$POLL"
    i=$((i + 1))
    tripped && { klog "disable marker appeared -> exiting"; exit 0; }

    pid=$(pidof surfaceflinger 2>/dev/null)
    if [ -n "$lastpid" ] && [ -n "$pid" ] && [ "$pid" != "$lastpid" ]; then
        n=$(restarts); n=$((n + 1)); set_restarts "$n"
        klog "surfaceflinger pid $lastpid -> $pid (restart $n/$K_RESTARTS)"
        [ "$n" -ge "$K_RESTARTS" ] && trip "surfaceflinger restarted ${n}x"
    fi
    lastpid=$pid

    now=$(lima_errs)
    if [ "$now" -ge $((base + K_LIMA_ERR)) ]; then
        trip "lima GP errors ${now} (baseline $base)"
    fi
done
klog "daemon finished ${MAX_POLLS} polls without tripping"
exit 0

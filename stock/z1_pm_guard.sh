#!/system/bin/sh
# R3: power-management and performance guard, runs once at sys.boot_completed.
# 1. Hold a kernel wake_lock so Android cannot suspend the box: the Z1 DT has
#    no wakeup-capable keys, so every suspend freezes userspace with the MCU
#    panel showing its last GRAM frame (looks alive) while USB ACM dies.
# 2. Kill animations and extend screen timeout: SwiftShader software
#    rendering on dual A7 plus a 2-hour screen timeout for bench work.
# 3. Dump the input/i2c/pm state so the ACM replay shows this round's facts.

log() { printf '<6>[z1pm] %s\n' "$*" > /dev/kmsg 2>/dev/null || true; }

if printf 'z1diag' > /sys/power/wake_lock 2>/dev/null; then
    log "wake_lock z1diag acquired: suspend disabled"
else
    log "wake_lock write FAILED; suspend not blocked"
fi

S=/system/bin/settings
if [ -x "$S" ]; then
    "$S" put global window_animation_scale 0.0 2>/dev/null && log "window animation scale = 0"
    "$S" put global transition_animation_scale 0.0 2>/dev/null && log "transition animation scale = 0"
    "$S" put global animator_duration_scale 0.0 2>/dev/null && log "animator duration scale = 0"
    "$S" put system screen_off_timeout 7200000 2>/dev/null && log "screen off timeout = 2h"
    "$S" put global stay_on_while_plugged_in 7 2>/dev/null && log "stay on while plugged (USB|AC)"
else
    log "settings tool missing; animation/timeout tuning skipped"
fi

log "input devices: $(grep -c '^N:' /proc/bus/input/devices 2>/dev/null) total"
grep -A4 '^N:' /proc/bus/input/devices 2>/dev/null | grep '^N:\|^H:' |
    while read -r line; do log "$line"; done

for p in sys.z1.radio.modules_ready sys.z1.radio.wifi_modules_ready; do
    log "$p=$(/system/bin/getprop "$p" 2>/dev/null)"
done
log "free/total mem: $(awk '/MemFree/{f=$2}/MemTotal/{t=$2}END{print f"/"t" kB"}' /proc/meminfo 2>/dev/null)"
log "pm_guard done"
exit 0

#!/system/bin/sh
# Recommended policy while the wake source story is unresolved:
#   * keep the z1diag kernel wake_lock (no suspend),
#   * but let the *display* power state follow the user's timeout, so the
#     backlight goes off and SurfaceFlinger stops compositing (SwiftShader
#     idle cost) without freezing userspace behind a stale frame.
# z1_pm_guard.sh currently does the opposite: 2 hour timeout + stay-on while
# plugged (fix3p ramdisk /z1_pm_guard.sh, "R3 ... for bench work").
PATH=/system/bin:/system/xbin:$PATH

# 30 s screen timeout, do not stay awake while charging
settings put system screen_off_timeout 30000
settings put global stay_on_while_plugged_in 0

# Keep the kernel wake_lock: with no verified wake source, a real suspend
# freezes userspace and kills USB ACM (see the script's own R3 comment).
grep -q z1diag /sys/power/wake_lock 2>/dev/null || echo z1diag > /sys/power/wake_lock

echo "screen_off_timeout=$(settings get system screen_off_timeout)"
echo "stay_on_while_plugged_in=$(settings get global stay_on_while_plugged_in)"
echo "wake_lock=$(cat /sys/power/wake_lock 2>/dev/null)"

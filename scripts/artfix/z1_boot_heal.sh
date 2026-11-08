#!/system/bin/sh
# v38: 首启自愈。若 system_server 在 system 变更后的第一次启动里崩溃循环、
# 迟迟到不了 boot_completed，就自动重启一次（只做一次，避免变成重启环）。
log() { printf '<6>[z1heal] %s\n' "$*" > /dev/kmsg 2>/dev/null || true; }
DONE=/data/misc/z1_heal_done
GRACE=${Z1_HEAL_GRACE:-240}
sleep "$GRACE"
[ "$(getprop sys.boot_completed 2>/dev/null)" = "1" ] && { log "boot completed; nothing to do"; exit 0; }
if [ -e "$DONE" ]; then log "already healed once for this system; not rebooting again"; exit 0; fi
n=$(dmesg 2>/dev/null | grep -c "FATAL EXCEPTION IN SYSTEM PROCESS")
if [ "${n:-0}" -lt 2 ]; then log "no system_server crash loop (n=$n); not rebooting"; exit 0; fi
mkdir -p /data/misc 2>/dev/null
( : > "$DONE" ) 2>/dev/null || true
sync
log "system_server crashed ${n}x and boot_completed=0 -> rebooting to recover"
reboot

#!/bin/bash
# Judge "is the Z1 boot actually progressing?" from a captured serial log, without waiting.
# usage: boot_progress.sh <serial.log>
L=${1:?serial log}
c(){ grep -a -c "$1" "$L" 2>/dev/null || true; }
n_boots=$(c "Linux version")
n_patchoat=$(c "patchoat")
n_prune=$(c "Preemptively pruning the dalvik cache")
n_noimg=$(c "Could not create image space")
n_attempt=$(c "Attempted image:")
n_genimg=$(grep -a "dex2oat" "$L" | grep -a -c -- "--image=" || true)
n_zygdie=$(grep -a "Service 'zygote'" "$L" | grep -a -c "received signal" || true)
n_sigill=$(c "SIGILL")
n_sigabrt=$(c "SIGABRT")
n_nexc=$(c "new_exception != nullptr")
n_bootdone=$(c "boot_completed")
n_ss=$(c "system_server")
last_uptime=$(grep -a -oE '^\[ *[0-9]+\.[0-9]+\]' "$L" | tail -1 | tr -dc '0-9.')

echo "== $L =="
printf "kernel boots      : %s%s\n" "$n_boots" "$( [ "$n_boots" -gt 1 ] && echo '   <-- bootloop / multi-capture')"
printf "last kernel uptime: %s s\n" "${last_uptime:-?}"
printf "patchoat lines    : %s\n" "$n_patchoat"
printf "prune dalvik-cache: %s\n" "$n_prune"
printf "image-space errors: %s   (Attempted image: %s)\n" "$n_noimg" "$n_attempt"
printf "dex2oat --image=  : %s   (on-device boot-image generation)\n" "$n_genimg"
printf "zygote killed     : %s\n" "$n_zygdie"
printf "SIGILL / SIGABRT  : %s / %s   (new_exception!=nullptr: %s)\n" "$n_sigill" "$n_sigabrt" "$n_nexc"
printf "system_server     : %s   boot_completed: %s\n" "$n_ss" "$n_bootdone"
echo -n "VERDICT: "
if [ "$n_boots" -gt 1 ]; then echo "BOOTLOOP ($n_boots kernel boots in one capture)"
elif [ "$n_bootdone" -gt 0 ]; then
  if [ "$n_patchoat" -gt 0 ] || [ "$n_prune" -gt 0 ]; then
    echo "OK but SLOW - reached sys.boot_completed, yet patchoat/prune ran (boot image NOT used)"
  else echo "OK - reached sys.boot_completed (see uptime above)"; fi
elif [ "$n_nexc" -gt 0 ] || [ "$n_sigill" -gt 0 ]; then echo "CRASHING (zygote/ART abort or SIGILL) - not progressing"
elif [ "$n_patchoat" -gt 0 ] || [ "$n_prune" -gt 0 ] || [ "$n_noimg" -gt 0 ]; then
     echo "IMAGE PATH FAILED -> slow imageless path (patchoat/prune/image-space error present)"
elif [ "$n_genimg" -gt 0 ]; then echo "ON-DEVICE BOOT-IMAGE GENERATION (~8-10 min, may abort)"
elif [ "$n_ss" -gt 0 ]; then echo "IN PROGRESS - system_server is up, waiting for boot_completed"
else echo "IN PROGRESS - pre-system_server"
fi
echo
echo "watch it live:   tail -f <log> | grep -aE \"boot_completed|patchoat|Preemptively|Attempted image|SIGILL|received signal|dex2oat --image\""
echo "healthy signs   : zero patchoat/prune/image-space lines, stable zygote pid, system_server within ~60-120 s,"
echo "                  then 'sys.boot_completed=1'"

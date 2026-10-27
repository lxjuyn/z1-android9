#!/system/bin/sh
# ---------------------------------------------------------------------------
# z1_diag_onboot.sh - unattended evidence capture on every boot
#
# Waits for sys.boot_completed=1, then another 90 s of settling, then runs
# z1_diag_all.sh with the tag "onboot". Intended for the ramdisk/init rc so a
# boot that later dies (lmkd kill, watchdog, black screen) still leaves a
# complete bundle on /data for the next session to pull.
#
# Suggested init.z1.rc service (ramdisk, runs as root):
#
#   service z1_diag_onboot /system/bin/sh /system/bin/z1_diag_onboot.sh
#       class late_start
#       user root
#       group root system
#       oneshot
#       disabled
#
#   on property:sys.boot_completed=1
#       start z1_diag_onboot
#
# If the scripts live in /data/local/z1diag instead of /system/bin, point the
# service at that absolute path; this script finds z1_diag_all.sh next to
# itself first, then falls back to the known locations.
#
# Kill switch:  touch /data/local/z1diag/onboot.disabled
# Delay knob :  Z1DIAG_ONBOOT_DELAY (seconds, default 90)
# Writes only /data/local/z1diag/
# ---------------------------------------------------------------------------

PATH=/system/bin:/system/xbin:/vendor/bin:/sbin:$PATH
export PATH
umask 022

BASE=/data/local/z1diag
DELAY=${Z1DIAG_ONBOOT_DELAY:-90}
case "$DELAY" in
    ''|*[!0-9]*) DELAY=90 ;;
esac

log() {
    printf '[z1diag-onboot] %s\n' "$*"
    printf '<6>[z1diag-onboot] %s\n' "$*" > /dev/kmsg 2>/dev/null || true
}

# Keep out of the OOM/lmkd line of fire while we work.
echo -900 > /proc/self/oom_score_adj 2>/dev/null || true

mkdir -p "$BASE" 2>/dev/null || true

if [ -e "$BASE/onboot.disabled" ]; then
    log "disabled by $BASE/onboot.disabled - not collecting"
    exit 0
fi

# ---------------------------------------------------------------------------
# locate z1_diag_all.sh
# ---------------------------------------------------------------------------
SELF=$0
case "$SELF" in
    /*) ;;
    *) SELF=$(pwd 2>/dev/null)/$SELF ;;
esac
SELFDIR=$(dirname "$SELF" 2>/dev/null)

ALL=
for c in "$SELFDIR/z1_diag_all.sh" /z1_diag_all.sh \
         /system/bin/z1_diag_all.sh /system/etc/z1_diag_all.sh \
         /data/local/z1diag/z1_diag_all.sh /cache/z1diag/z1_diag_all.sh; do
    if [ -f "$c" ]; then
        ALL=$c
        break
    fi
done

if [ -z "$ALL" ]; then
    log "z1_diag_all.sh not found next to $SELF nor in /system/bin - abort"
    exit 1
fi
log "collector: $ALL"

# ---------------------------------------------------------------------------
# wait for boot completion (max 10 min), then the settle delay
# ---------------------------------------------------------------------------
i=0
while [ $i -lt 600 ]; do
    BC=$(getprop sys.boot_completed 2>/dev/null)
    if [ "$BC" = "1" ]; then
        log "sys.boot_completed=1 after ${i}s"
        break
    fi
    sleep 5
    i=$((i + 5))
done

if [ "$(getprop sys.boot_completed 2>/dev/null)" != "1" ]; then
    log "boot never completed in 600s; collecting anyway (that IS the evidence)"
fi

log "waiting ${DELAY}s for the boot wave to settle"
sleep "$DELAY"

# Another init could have started a manual run; never overlap.
if [ -d "$BASE/.running" ]; then
    log "$BASE/.running exists (manual run in progress) - skipping onboot pass"
    exit 0
fi

log "starting collection"
sh "$ALL" onboot
RC=$?
log "collection finished rc=$RC"

# keep only the newest few bundles so /data cannot fill up.
# NOTE: the trailing slash matters - without it the glob also matches the
# *.tar.gz files and the "newest 6" rotation would mix two different sets.
KEEP=6
n=$(ls -1d "$BASE"/collect_*/ 2>/dev/null | wc -l)
if [ -n "$n" ] && [ "$n" -gt "$KEEP" ]; then
    ls -1dt "$BASE"/collect_*/ 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r old; do
        log "pruning $old"
        rm -rf "$old" 2>/dev/null || true
    done
fi
nt=$(ls -1 "$BASE"/collect_*.tar.gz 2>/dev/null | wc -l)
if [ -n "$nt" ] && [ "$nt" -gt "$KEEP" ]; then
    ls -1t "$BASE"/collect_*.tar.gz 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r old; do
        log "pruning $old"
        rm -f "$old" 2>/dev/null || true
    done
fi

exit 0

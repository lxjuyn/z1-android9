#!/system/bin/sh
# ---------------------------------------------------------------------------
# z1stage_boot.sh -- Z1 deployment channel: boot-time stage seeder + applier.
#
# Runs as ROOT from Android init (see init_fragment.rc):
#     on post-fs-data
#         exec u:r:init:s0 root root -- /system/bin/sh /z1stage_boot.sh
#
# This action is synchronous (init waits for it) and must finish before
# class_start hal starts vendor.gralloc-2-0 / vendor.hwcomposer-2-1 (~2.5 s
# of headroom), so this script NEVER sleeps and only does a handful of small
# file operations.
#
# What it does
#   1. /data/local/tmp/z1stage  -> mode 0777, owner 2000:2000 (AID shell),
#      plus a world-writable "active" subdirectory.
#   2. Every candidate baked into the ramdisk root (/hwcomposer.z1.so,
#      /gralloc.z1.so, /z1kmstest, /z1rootd.sh, when present) is refreshed as
#      $STAGE/<name>.ramdisk, and copied to $STAGE/<name> only when that file
#      does not exist yet -- an operator's working copy is never clobbered.
#   3. The "active" install set is applied to /vendor/lib/hw:
#        active/hwcomposer.z1.so -> /vendor/lib/hw/hwcomposer.z1.so  (0644)
#        active/gralloc.z1.so    -> /vendor/lib/hw/gralloc.z1.so     (0644)
#      Absence of an active file REMOVES the corresponding vendor override,
#      which restores the stock module in /system/lib/hw (the original HWC1
#      hwcomposer.z1.so / gralloc.z1.so).  A9 libhardware resolves the module
#      as <class>.<variant>.so with variant = ro.hardware / ro.product.board
#      (= z1) and searches /odm/lib/hw -> /vendor/lib/hw -> /system/lib/hw,
#      so the /vendor copy wins while it exists and the /system one is used
#      once it is gone.
#
# The install uses cp-to-tmp + rename so a live re-run can never rewrite an
# mmap'ed .so in place (that would SIGBUS the running composer).  The normal
# boot path runs before any graphics service exists, but the rename is free.
#
# All diagnostics go to /dev/kmsg with the "z1stage:" prefix: on this board
# that is the only log channel that reliably reaches the serial console.
#
# The script always exits 0: a broken /data or a missing staging directory
# must never hold the boot.
# ---------------------------------------------------------------------------

STAGE=/data/local/tmp/z1stage
VENDOR_HW=/vendor/lib/hw
SHELL_UID=2000          # AID_SHELL
SHELL_GID=2000          # AID_SHELL

MKDIR=/system/bin/mkdir
CHMOD=/system/bin/chmod
CHOWN=/system/bin/chown
CP=/system/bin/cp
MV=/system/bin/mv
RM=/system/bin/rm
SETPROP=/system/bin/setprop
RESTORECON=/system/bin/restorecon
SHA256SUM=/system/bin/sha256sum
WC=/system/bin/wc

log() { printf 'z1stage: %s\n' "$*" >> /dev/kmsg 2>/dev/null; }

uptime_s() { read -r up _rest < /proc/uptime 2>/dev/null; printf '%s' "${up:-?}"; }

file_size() { set -- $($WC -c < "$1" 2>/dev/null); printf '%s' "${1:-?}"; }

sha_of() {
    if [ -x "$SHA256SUM" ]; then
        set -- $($SHA256SUM "$1" 2>/dev/null)
        printf '%s' "${1:-unavailable}"
    else
        printf '%s' 'sha256sum-missing'
    fi
}

set_prop() { [ -x "$SETPROP" ] && "$SETPROP" "$1" "$2" 2>/dev/null; return 0; }

T0=$(uptime_s)
SEEDED=0
COPIED=0
APPLIED=0
REMOVED=0
ERRORS=0

# --- 1. stage directory ----------------------------------------------------
$MKDIR -p "$STAGE" 2>/dev/null
if [ ! -d "$STAGE" ]; then
    log "FATAL cannot create ${STAGE} (is /data mounted rw?)"
    exit 0
fi
$MKDIR -p "$STAGE/active" 2>/dev/null
$CHMOD 0777 "$STAGE" "$STAGE/active" 2>/dev/null
$CHOWN "$SHELL_UID:$SHELL_GID" "$STAGE" "$STAGE/active" 2>/dev/null
[ -w "$STAGE" ] || log "WARN ${STAGE} is not writable by this process"

# --- 2. seed the candidates that are baked into the ramdisk ----------------
# seed <ramdisk-name> <mode>
seed() {
    _name=$1
    _mode=$2
    _src="/$_name"
    _ram="$STAGE/$_name.ramdisk"
    _dst="$STAGE/$_name"

    if [ ! -f "$_src" ]; then
        log "ramdisk /${_name}: absent (nothing to seed)"
        return 0
    fi

    if $CP "$_src" "$_ram" 2>/dev/null; then
        $CHMOD "$_mode" "$_ram" 2>/dev/null
        $CHOWN "$SHELL_UID:$SHELL_GID" "$_ram" 2>/dev/null
        SEEDED=$((SEEDED + 1))
        log "seeded /${_name} -> ${_name}.ramdisk ($(file_size "$_ram") B, mode ${_mode}, sha256 $(sha_of "$_ram"))"
    else
        ERRORS=$((ERRORS + 1))
        log "ERROR cannot write ${_ram}"
    fi

    if [ -e "$_dst" ]; then
        log "working copy ${_name}: kept existing ($(file_size "$_dst") B), not overwritten"
    elif $CP "$_src" "$_dst" 2>/dev/null; then
        $CHMOD "$_mode" "$_dst" 2>/dev/null
        $CHOWN "$SHELL_UID:$SHELL_GID" "$_dst" 2>/dev/null
        COPIED=$((COPIED + 1))
        log "working copy ${_name}: created ($(file_size "$_dst") B, sha256 $(sha_of "$_dst"))"
    else
        ERRORS=$((ERRORS + 1))
        log "ERROR cannot create ${_dst}"
    fi
}

# --- 3. apply the active set ----------------------------------------------
# apply <file-name> <vendor target>
apply() {
    _name=$1
    _dst=$2
    _active="$STAGE/active/$_name"
    _tmp="$_dst.z1tmp"
    _prop="z1stage.${_name%%.*}"

    if [ -f "$_active" ]; then
        if ! $CP "$_active" "$_tmp" 2>/dev/null; then
            ERRORS=$((ERRORS + 1))
            log "ERROR active/${_name}: cannot write ${_tmp} (/vendor ro?) -- leaving ${_dst} untouched"
            return 0
        fi
        $CHMOD 0644 "$_tmp" 2>/dev/null
        $CHOWN 0:0 "$_tmp" 2>/dev/null
        if $MV -f "$_tmp" "$_dst" 2>/dev/null; then
            [ -x "$RESTORECON" ] && "$RESTORECON" "$_dst" >/dev/null 2>&1
            APPLIED=$((APPLIED + 1))
            log "active ${_name} -> ${_dst} ($(file_size "$_dst") B, mode 0644, sha256 $(sha_of "$_dst"))"
            set_prop "$_prop" active
        else
            ERRORS=$((ERRORS + 1))
            $RM -f "$_tmp" 2>/dev/null
            log "ERROR active/${_name}: rename ${_tmp} -> ${_dst} failed -- leaving old ${_dst}"
        fi
        return 0
    fi

    if [ -e "$_dst" ]; then
        if $RM -f "$_dst" 2>/dev/null; then
            REMOVED=$((REMOVED + 1))
            log "no active/${_name} -> removed ${_dst} (stock /system/lib/hw/${_name} takes over)"
            set_prop "$_prop" system
        else
            ERRORS=$((ERRORS + 1))
            log "ERROR no active/${_name}: cannot remove ${_dst} (/vendor ro?)"
        fi
    else
        log "no active/${_name} -> ${_dst} already absent (stock /system/lib/hw/${_name} in use)"
        set_prop "$_prop" system
    fi
    return 0
}

log "begin @${T0}s (stage ${STAGE})"

seed hwcomposer.z1.so 0644
seed gralloc.z1.so 0644
seed z1kmstest 0755
seed z1rootd.sh 0755

# --- 2b. one-shot auto-install of the ramdisk HWC candidate -----------------
# The first boot after a flash tests the HAL that was baked into the ramdisk,
# so a single physical flash yields both the root channel AND the first HAL
# result.  A marker file makes this happen exactly once: if the candidate is
# broken (no display), a plain reboot falls back to the stock HWC1 in
# /system/lib/hw without any operator action.  gralloc is deliberately NOT
# auto-installed -- it is the whole-UI allocator.
AUTO_MARK="$STAGE/.hwc_auto_installed"
AUTO_CAND="$STAGE/hwcomposer.z1.so"
if [ -f "$AUTO_MARK" ]; then
    log "auto-install: already done once (${AUTO_MARK}), skipped"
elif [ ! -f "$AUTO_CAND" ]; then
    log "auto-install: no candidate at ${AUTO_CAND}, skipped"
else
    if $CP "$AUTO_CAND" "$STAGE/active/hwcomposer.z1.so" 2>/dev/null; then
        $CHMOD 0644 "$STAGE/active/hwcomposer.z1.so" 2>/dev/null
        $CHOWN "$SHELL_UID:$SHELL_GID" "$STAGE/active/hwcomposer.z1.so" 2>/dev/null
        printf 'auto-installed %s at %ss\n' "$(sha_of "$AUTO_CAND")" "$(uptime_s)" \
            > "$AUTO_MARK" 2>/dev/null
        $CHMOD 0666 "$AUTO_MARK" 2>/dev/null
        log "auto-install: seeded active/hwcomposer.z1.so from ${AUTO_CAND} (sha256 $(sha_of "$AUTO_CAND")), marker ${AUTO_MARK} written"
    else
        ERRORS=$((ERRORS + 1))
        log "ERROR auto-install: cannot create active/hwcomposer.z1.so"
    fi
fi

# --- 2c. debugfs (optional) -------------------------------------------------
# sw_sync (CONFIG_SW_SYNC) exposes <debugfs>/sync/sw_sync; the HWC2 HAL uses it
# for release fences when debug.z1.hwc.fences=1.  Android's init.rc never mounts
# debugfs on this image, so do it here.  Failure is harmless.
if [ ! -e /sys/kernel/debug/sync/sw_sync ]; then
    /system/bin/mkdir -p /sys/kernel/debug 2>/dev/null
    /system/bin/mount -t debugfs none /sys/kernel/debug 2>/dev/null
    if [ -e /sys/kernel/debug/sync/sw_sync ]; then
        log "debugfs mounted, sw_sync available"
    else
        log "sw_sync not available (debugfs mount failed or CONFIG_SW_SYNC=n); fences stay -1"
    fi
fi

if [ -d "$VENDOR_HW" ]; then
    apply hwcomposer.z1.so "$VENDOR_HW/hwcomposer.z1.so"
    apply gralloc.z1.so "$VENDOR_HW/gralloc.z1.so"
else
    ERRORS=$((ERRORS + 1))
    log "ERROR ${VENDOR_HW} missing; active/ set NOT applied"
fi

T1=$(uptime_s)
log "done @${T1}s: seeded=${SEEDED} working_copies=${COPIED} applied=${APPLIED} removed=${REMOVED} errors=${ERRORS}"
exit 0

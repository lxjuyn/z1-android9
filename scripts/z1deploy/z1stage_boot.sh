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
GETPROP=/system/bin/getprop
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

# --- 2b. one-shot auto-install of the ramdisk candidates --------------------
# The first boot after a flash tests the modules that were baked into the
# ramdisk, so one physical flash yields both the root channel AND the first
# real OVL result.  That has to cover BOTH modules: the HWC2 compositor is what
# programs the OVL planes, and the gralloc is what makes the layer buffers
# physically contiguous so those planes can scan them out.  With the stock
# (lima) gralloc every layer is non-contiguous and every frame falls back to
# client composition, i.e. nothing of the OVL path gets exercised.
#
# IMPORTANT: the auto-installed copy must NOT stay in active/, otherwise a
# broken module would be re-installed forever (and the legacy pre-v83 rc line
# 42 that copies /hwcomposer.z1.so into /vendor on every boot would keep it
# alive even without active/).  So: apply it for this boot only, then delete
# the active copy.  On the next boot active/ is empty again, the "apply" step
# below removes whatever the legacy line installed, and libhardware falls back
# to the stock module in /system/lib/hw.  An explicit install via
# z1_install_hal.sh writes active/ itself and therefore persists.
# One marker per module so a half-broken pair can be rolled back separately.
AUTO_ONCE_HWC=0
AUTO_ONCE_GRALLOC=0
auto_once() {   # auto_once <file> <marker-suffix> <once-var-name>
    _name=$1
    _mark="$STAGE/.${2}_auto_installed"
    _cand="$STAGE/$_name"

    if [ -f "$_mark" ]; then
        log "auto-install ${_name}: already done once (${_mark}), skipped"
        return 0
    fi
    if [ ! -f "$_cand" ]; then
        log "auto-install ${_name}: no candidate at ${_cand}, skipped"
        return 0
    fi
    if $CP "$_cand" "$STAGE/active/$_name" 2>/dev/null; then
        $CHMOD 0644 "$STAGE/active/$_name" 2>/dev/null
        $CHOWN "$SHELL_UID:$SHELL_GID" "$STAGE/active/$_name" 2>/dev/null
        printf 'auto-installed %s at %ss\n' "$(sha_of "$_cand")" "$(uptime_s)" \
            > "$_mark" 2>/dev/null
        $CHMOD 0666 "$_mark" 2>/dev/null
        log "auto-install ${_name}: applying for this boot only (sha256 $(sha_of "$_cand")), marker ${_mark} written"
        eval "${3}=1"
    else
        ERRORS=$((ERRORS + 1))
        log "ERROR auto-install ${_name}: cannot create active/${_name}"
    fi
}

auto_once hwcomposer.z1.so hwc AUTO_ONCE_HWC
auto_once gralloc.z1.so gralloc AUTO_ONCE_GRALLOC

# --- 2b2. dma-heap device node (needed for contiguous scanout buffers) ------
# The kernel registers the CMA heap as /sys/class/dma_heap/default_cma_region
# (drivers/dma-buf/dma-heap.c, dma_heap_devnode() returns "dma_heap/<name>"),
# but on this image ueventd has never materialised /dev/dma_heap/*, so gralloc
# falls back to lima and the display cannot scan the buffers out.  Create the
# node ourselves from the sysfs major:minor.  Cheap, idempotent, and it also
# reports whether the kernel created the heap at all.
if [ -e /sys/class/dma_heap ]; then
    for _h in /sys/class/dma_heap/*; do
        [ -e "$_h/dev" ] || continue
        _n=${_h##*/}
        _d=/dev/dma_heap/$_n
        if [ -e "$_d" ]; then
            # ueventd may have created it 0600 root:root; the allocator runs
            # as `system` and must be able to open it.
            $CHMOD 0666 "$_d" 2>/dev/null
            log "dma_heap ${_n}: node already present (mode fixed to 0666)"
            continue
        fi
        _dev=$(cat "$_h/dev" 2>/dev/null)
        _maj=${_dev%%:*}
        _min=${_dev##*:}
        if [ -n "$_maj" ] && [ -n "$_min" ]; then
            $MKDIR -p /dev/dma_heap 2>/dev/null
            if /system/bin/mknod "$_d" c "$_maj" "$_min" 2>/dev/null; then
                $CHMOD 0666 "$_d" 2>/dev/null
                log "dma_heap ${_n}: mknod ${_d} c ${_maj}:${_min} ok"
            else
                log "ERROR dma_heap ${_n}: mknod ${_d} c ${_maj}:${_min} failed"
            fi
        else
            log "ERROR dma_heap ${_n}: cannot read major:minor"
        fi
    done
else
    log "WARN /sys/class/dma_heap absent: kernel did not register a CMA heap"
fi

# --- 2b3. /dev/graphics/fb0 for the stock goldfish HWC1 fallback ------------
# The stock module in /system/lib/hw/hwcomposer.z1.so opens the framebuffer as
# "/dev/graphics/fb0" (AOSP goldfish hwcomposer.cpp, hwc_open ->
# open("/dev/graphics/fb0")).  On this image that path has never worked: the
# last boot log has 279 "could not open framebuffer".  Android's ueventd puts
# class "graphics" devices under /dev/graphics (ueventd.rc: subsystem graphics
# / dirname /dev/graphics), so a missing node means ueventd never processed the
# fb0 uevent.  Recreate it from sysfs so that removing our HAL really restores
# a live display instead of leaving the panel without any driver.
FB_SYS=/sys/class/graphics/fb0
if [ ! -e /dev/graphics/fb0 ] && [ -r "$FB_SYS/dev" ]; then
    _dev=$(cat "$FB_SYS/dev" 2>/dev/null)
    _maj=${_dev%%:*}
    _min=${_dev##*:}
    $MKDIR -p /dev/graphics 2>/dev/null
    if [ -n "$_maj" ] && [ -n "$_min" ] && \
       /system/bin/mknod /dev/graphics/fb0 c "$_maj" "$_min" 2>/dev/null; then
        $CHMOD 0660 /dev/graphics/fb0 2>/dev/null
        $CHOWN 0:1003 /dev/graphics/fb0 2>/dev/null
        log "fb0: created /dev/graphics/fb0 c ${_maj}:${_min} (stock HWC1 fallback becomes usable)"
    else
        log "ERROR fb0: cannot create /dev/graphics/fb0 (${_maj}:${_min})"
    fi
elif [ -e /dev/graphics/fb0 ]; then
    log "fb0: /dev/graphics/fb0 already present"
else
    log "fb0: ${FB_SYS} not present yet (drm fbdev not registered); fallback node not created"
fi

# --- 2c. debugfs (optional) -------------------------------------------------
# sw_sync (CONFIG_SW_SYNC) exposes <debugfs>/sync/sw_sync; the HWC2 HAL uses it
# for release fences when debug.z1.hwc.fences=1.  Android's init.rc never mounts
# debugfs on this image, so do it here.  Failure is harmless.
if [ -e /sys/kernel/debug/sync/sw_sync ]; then
    $CHMOD 0666 /sys/kernel/debug/sync/sw_sync 2>/dev/null
fi
if [ ! -e /sys/kernel/debug/sync/sw_sync ]; then
    /system/bin/mkdir -p /sys/kernel/debug 2>/dev/null
    /system/bin/mount -t debugfs none /sys/kernel/debug 2>/dev/null
    if [ -e /sys/kernel/debug/sync/sw_sync ]; then
        # sw_sync creates the file 0600 root; the composer runs as `system`.
        $CHMOD 0666 /sys/kernel/debug/sync/sw_sync 2>/dev/null
        log "debugfs mounted, sw_sync available (mode $(ls -l /sys/kernel/debug/sync/sw_sync 2>/dev/null | cut -c1-10))"
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

if [ "${AUTO_ONCE_HWC:-0}" = 1 ]; then
    $RM -f "$STAGE/active/hwcomposer.z1.so" 2>/dev/null
    log "auto-install: removed the one-shot HWC copy -> next boot falls back to the stock HWC1 unless an operator installs one"
fi
if [ "${AUTO_ONCE_GRALLOC:-0}" = 1 ]; then
    $RM -f "$STAGE/active/gralloc.z1.so" 2>/dev/null
    log "auto-install: removed the one-shot gralloc copy -> next boot falls back to the stock gralloc unless an operator installs one"
fi

# --- 3b. identity properties ------------------------------------------------
# Which binary is actually in effect must be visible on the board, because there
# are three installation paths (legacy rc line 42, the one-shot above, an
# operator's active/ set) and /vendor survives a boot.img rollback.
for _m in hwcomposer gralloc; do
    _p="$VENDOR_HW/$_m.z1.so"
    if [ -f "$_p" ]; then
        set_prop "z1stage.${_m}.sha" "$(sha_of "$_p")"
    else
        set_prop "z1stage.${_m}.sha" "system:$(sha_of "/system/lib/hw/$_m.z1.so")"
    fi
done
log "identity: hwcomposer=$($GETPROP z1stage.hwcomposer.sha 2>/dev/null) gralloc=$($GETPROP z1stage.gralloc.sha 2>/dev/null)"

T1=$(uptime_s)
log "done @${T1}s: seeded=${SEEDED} working_copies=${COPIED} applied=${APPLIED} removed=${REMOVED} errors=${ERRORS}"
exit 0

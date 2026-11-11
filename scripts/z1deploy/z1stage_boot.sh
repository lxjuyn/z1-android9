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

log "begin @${T0}s (stage ${STAGE})"

# --- 1b. filesystem integrity: repair ONLY after an unclean shutdown ------
# The board is routinely power-cycled with the power key.  A hard cut leaves
# the ext4 journal dirty; the kernel then *replays* it (fixing the journal but
# not the metadata residue), after which fs_mgr considers the fs clean and
# never runs e2fsck.  The kernel later trips over the residue:
#   EXT4-fs error (device mmcblk0p6): ext4_mb_generate_buddy:1314: group 16,
#       block bitmap and bg descriptor inconsistent
#   EXT4-fs (mmcblk0p6): Remounting filesystem read-only
# and then every /data writer gets EROFS (dropbox, dalvik-cache), so zygote
# SIGABRTs every ~5 s and the boot animation never ends.
#
# Signals (both cheap, neither needs a healthy filesystem):
#   1. dmesg contains "EXT4-fs (<dev>): recovery complete" -- the kernel only
#      prints that when it had to replay a journal, i.e. the previous session
#      did not unmount.  This is the authoritative "hard power cut" flag.
#   2. s_state bit1 (EXT4_ERROR_FS, superblock offset 1024+58) -- the kernel
#      recorded an error on this filesystem.
#   NOTE: s_state bit0 (EXT4_VALID_FS) is NOT usable here: it is only
#   maintained for non-journaled filesystems, and empirically stays 1 across a
#   mount of a journaled ext4 (verified 2026-11-11 on a 64 MiB test image:
#   mkfs/mounted-rw/clean-unmount all read 0x0001).
#
# Clean boots therefore pay nothing; a hard cut is repaired before the
# framework starts.
sb_state() {   # <blockdev> -> "<magic> <state>"
    dd if="$1" bs=1 skip=1080 count=4 2>/dev/null | od -An -tu2 2>/dev/null | tr -s ' '
}

fs_needs_repair() {   # <blockdev>; return 0 when a repair is warranted
    _dev=$1
    _name=${1##*/}
    if dmesg 2>/dev/null | grep -q "EXT4-fs ($_name): recovery complete"; then
        log "$_name: journal was replayed (previous shutdown killed) -> REPAIR"
        return 0
    fi
    _pair=$(sb_state "$_dev")
    set -- $_pair
    _magic=$1
    _state=$2
    if [ "$_magic" = "61267" ] && [ -n "$_state" ] && [ $((_state & 2)) -ne 0 ]; then
        log "$_name: s_state=0x$(printf '%04x' "$_state") has EXT4_ERROR_FS -> REPAIR"
        return 0
    fi
    log "$_name: no journal replay, s_state=$( [ -n "$_state" ] && printf '0x%04x' "$_state" || echo unreadable) -> clean, skip"
    return 1
}

repair_fs() {   # <mountpoint> <blockdev>
    if /system/bin/umount "$1" 2>/dev/null; then
        _o=$(/system/bin/e2fsck -f -y "$2" 2>&1)
        _rc=$?
        /system/bin/mount -o rw,seclabel,nosuid,nodev,noatime "$2" "$1"
        _mrc=$?
        log "repair $1: e2fsck rc=${_rc} remount_rc=${_mrc} tail=$(printf '%s' "$_o" | tail -1)"
    else
        log "repair $1: busy -> skipped (fs_mgr owns the mount)"
    fi
}

if [ -x /system/bin/e2fsck ]; then
    fs_needs_repair /dev/block/mmcblk0p6 && repair_fs /data  /dev/block/mmcblk0p6
    fs_needs_repair /dev/block/mmcblk0p5 && repair_fs /cache /dev/block/mmcblk0p5
else
    log "fs repair: /system/bin/e2fsck absent -> skipped"
fi

seed hwcomposer.z1.so 0644
seed gralloc.z1.so 0644
seed z1kmstest 0755
seed z1rootd.sh 0755

# --- 2b. install the candidates baked into the ramdisk ----------------------
# Semantics (simple and predictable, because a wedged console must never be
# required to recover the display):
#
#   $STAGE/active/<name>  wins if the operator put one there   -> install it
#   else /<name> (ramdisk) is installed every boot             -> the image wins
#   else the /vendor override is REMOVED                       -> stock module
#
# A kill switch exists for a broken HAL/gralloc: create the file
# $STAGE/.disable_<module> (hwcomposer / gralloc) and reboot (or run the same
# logic live from the root channel) -- the /vendor override is then removed and
# libhardware falls back to /system/lib/hw, which is the stock module.
#
# The install is cp-to-tmp + rename, so a live re-run can never rewrite a
# mmap'ed .so in place (that would SIGBUS the running composer).
install_one() {   # install_one <file-name> <disable-token>
    _name=$1
    _tok=$2
    _active="$STAGE/active/$_name"
    _kill="$STAGE/.disable_$_tok"
    _dst="$VENDOR_HW/$_name"
    _tmp="$_dst.z1tmp"

    if [ -f "$_kill" ]; then
        log "install ${_name}: kill switch ${_kill} present -> /vendor override removed"
        $RM -f "$_dst" 2>/dev/null
        return 0
    fi

    if [ -f "$_active" ]; then
        _src="$_active"
    elif [ -f "/$_name" ]; then
        _src="/$_name"
    else
        if [ -e "$_dst" ]; then
            $RM -f "$_dst" 2>/dev/null && log "install ${_name}: no candidate -> removed ${_dst} (stock module takes over)"
        fi
        return 0
    fi

    if $CP "$_src" "$_tmp" 2>/dev/null; then
        $CHMOD 0644 "$_tmp" 2>/dev/null
        $CHOWN 0:0 "$_tmp" 2>/dev/null
        if $MV -f "$_tmp" "$_dst" 2>/dev/null; then
            APPLIED=$((APPLIED + 1))
            log "install ${_name}: ${_src} -> ${_dst} (sha256 $(sha_of "$_dst"))"
        else
            ERRORS=$((ERRORS + 1))
            $RM -f "$_tmp" 2>/dev/null
            log "ERROR install ${_name}: rename ${_tmp} -> ${_dst} failed"
        fi
    else
        ERRORS=$((ERRORS + 1))
        log "ERROR install ${_name}: cannot write ${_tmp} (/vendor ro?)"
    fi
}

install_one hwcomposer.z1.so hwcomposer
install_one gralloc.z1.so gralloc

# --- 2b1. system files staged in the ramdisk (reliable replacement for the
# lossy serial push) -------------------------------------------------------
# A file baked into the ramdisk as /<src> is installed to <dst> when it is
# present.  When the source is absent the destination is LEFT ALONE -- unlike
# install_one() there is no "remove on absence", because these files live in
# /system and a ramdisk without them must never regress the system image.
install_sys_file() {   # install_sys_file <src-in-ramdisk> <dst>
    _src="/$1"
    _dst="$2"
    _tmp="${_dst}.z1tmp"
    [ -f "$_src" ] || return 0
    if $CP "$_src" "$_tmp" 2>/dev/null; then
        $CHMOD 0644 "$_tmp" 2>/dev/null
        $CHOWN 0:0 "$_tmp" 2>/dev/null
        if $MV -f "$_tmp" "$_dst" 2>/dev/null; then
            APPLIED=$((APPLIED + 1))
            log "install ${1}: -> ${_dst} (sha256 $(sha_of "$_dst"))"
        else
            ERRORS=$((ERRORS + 1)); $RM -f "$_tmp" 2>/dev/null
            log "ERROR install ${1}: rename to ${_dst} failed"
        fi
    else
        ERRORS=$((ERRORS + 1))
        log "ERROR install ${1}: cannot write ${_tmp}"
    fi
}

install_sys_file z1_libwifi-hal.so          /system/vendor/lib/libwifi-hal.so
install_sys_file z1_libbt-vendor.so         /system/vendor/lib/libbt-vendor.so
install_sys_file z1_audio.primary.mt6572.so /system/vendor/lib/hw/audio.primary.mt6572.so


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

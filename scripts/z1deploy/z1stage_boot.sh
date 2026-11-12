#!/system/bin/sh
# ---------------------------------------------------------------------------
# z1stage_boot.sh -- Z1 deployment channel: boot-time stage seeder + applier.
#
# Runs as ROOT from Android init in two stages:
#   on fs, immediately after mount_all:
#     exec u:r:init:s0 root root -- /system/bin/sh /z1stage_boot.sh --storage-only
#     wait_for_prop sys.z1.storage_ready 1
#   on post-fs-data, replacing the old stage invocation:
#     exec u:r:init:s0 root root -- /system/bin/sh /z1stage_boot.sh --skip-storage
# The early invocation may run fsck.  The later invocation checks the gate,
# installs payloads, and configures device nodes and runtime policy.
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
#      An active file wins, otherwise the ramdisk candidate is installed.
#      With neither source (or with a kill switch), the override is removed,
#      restoring the stock module in /system/lib/hw (the original HWC1
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
# Filesystem and installation failures publish z1stage.status and exit nonzero.
# init must gate downstream writers on sys.z1.storage_ready=1; an exec return code
# alone does not stop Android init from processing later actions.
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
CMP=/system/bin/cmp
SETPROP=/system/bin/setprop
RESTORECON=/system/bin/restorecon
SHA256SUM=/system/bin/sha256sum
GETPROP=/system/bin/getprop
WC=/system/bin/wc
MOUNT=/system/bin/mount
UMOUNT=/system/bin/umount
FSCK=/system/bin/e2fsck
READLINK=/system/bin/readlink
DMESG=/system/bin/dmesg
PROC_MOUNTS=/proc/mounts

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

set_checked_prop() {
    if [ -x "$SETPROP" ] && [ -x "$GETPROP" ] &&
       "$SETPROP" "$1" "$2" 2>/dev/null &&
       _scp_value=$("$GETPROP" "$1" 2>/dev/null) &&
       [ "$_scp_value" = "$2" ]; then
        return 0
    fi
    log "BLOCKED: property $1=$2 could not be written/read back"
    return 1
}

T0=$(uptime_s)
SEEDED=0
COPIED=0
APPLIED=0
REMOVED=0
ERRORS=0

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

    if $CP "$_src" "$_ram" 2>/dev/null &&
       $CHMOD "$_mode" "$_ram" 2>/dev/null &&
       $CHOWN "$SHELL_UID:$SHELL_GID" "$_ram" 2>/dev/null; then
        SEEDED=$((SEEDED + 1))
        log "seeded /${_name} -> ${_name}.ramdisk ($(file_size "$_ram") B, mode ${_mode}, sha256 $(sha_of "$_ram"))"
    else
        ERRORS=$((ERRORS + 1))
        log "ERROR cannot write ${_ram}"
    fi

    if [ -e "$_dst" ]; then
        log "working copy ${_name}: kept existing ($(file_size "$_dst") B), not overwritten"
    elif $CP "$_src" "$_dst" 2>/dev/null &&
         $CHMOD "$_mode" "$_dst" 2>/dev/null &&
         $CHOWN "$SHELL_UID:$SHELL_GID" "$_dst" 2>/dev/null; then
        COPIED=$((COPIED + 1))
        log "working copy ${_name}: created ($(file_size "$_dst") B, sha256 $(sha_of "$_dst"))"
    else
        ERRORS=$((ERRORS + 1))
        log "ERROR cannot create ${_dst}"
    fi
}

log "begin @${T0}s (stage ${STAGE})"

# --- 1. filesystem gate: run before creating or writing anything in /data ---
# e2fsck operates only after this device has been unmounted everywhere.  Return
# codes are a bitmask: 0/1 allow RW, 2/3 request reboot, all others fail closed.
# A missing/foreign mount, unreadable superblock, failed dmesg read or busy
# unmount is never reported as clean.  Diagnostics stay in /dev/kmsg.
sb_state() {   # <blockdev> -> "<magic> <state>"
    dd if="$1" bs=1 skip=1080 count=4 2>/dev/null | od -An -tu2 2>/dev/null | tr -s ' '
}

mount_record() {   # <mountpoint>; exactly one source + options, or absent
    [ -r "$PROC_MOUNTS" ] || return 2
    awk -v point="$1" '$2 == point { n++; line=$1 " " $4 }
        END { if (n > 1) exit 2; if (n == 1) print line; else print "absent" }' "$PROC_MOUNTS"
}

device_unmounted() (   # <blockdev>; reject aliases mounted at any path
    _du_expected=$($READLINK -f "$1" 2>/dev/null) || exit 1
    [ -n "$_du_expected" ] && [ -r "$PROC_MOUNTS" ] || exit 1
    while read -r _du_src _du_point _du_type _du_opts _du_rest; do
        case "$_du_src" in /*)
            _du_real=$($READLINK -f "$_du_src" 2>/dev/null) || exit 1
            [ "$_du_real" != "$_du_expected" ] || exit 1 ;;
        esac
    done < "$PROC_MOUNTS"
    exit 0
)

fs_needs_repair() (   # 0=repair, 1=clean, 2=unknown/unreadable
    _fn_dev=$1
    _fn_name=${1##*/}
    _fn_pair=$(sb_state "$_fn_dev")
    set -- $_fn_pair
    [ "$#" -eq 2 ] && [ "$1" = 61267 ] || {
        log "$_fn_name: unreadable/invalid ext4 superblock -> BLOCKED"; exit 2;
    }
    case "$2" in ''|*[!0-9]*) log "$_fn_name: invalid ext4 state -> BLOCKED"; exit 2 ;; esac
    if [ $(($2 & 2)) -ne 0 ]; then
        log "$_fn_name: EXT4_ERROR_FS -> REPAIR"
        exit 0
    fi
    _fn_dmesg=$($DMESG 2>/dev/null) || {
        log "$_fn_name: cannot check journal replay -> BLOCKED"; exit 2;
    }
    case "$_fn_dmesg" in
        *"EXT4-fs ($_fn_name): recovery complete"*)
            log "$_fn_name: journal replay -> REPAIR"
            exit 0 ;;
    esac
    log "$_fn_name: readable superblock, no error flag or journal replay"
    exit 1
)

repair_fs() (   # <mountpoint> <blockdev> <mounted|absent>
    _rf_point=$1
    _rf_dev=$2
    if [ ! -x "$FSCK" ]; then
        log "repair $_rf_point: e2fsck unavailable -> BLOCKED"; exit 1
    fi
    if [ "$3" = mounted ] && ! "$UMOUNT" "$_rf_point" 2>/dev/null; then
        log "repair $_rf_point: busy/unmount failed -> BLOCKED"; exit 1
    fi
    if ! device_unmounted "$_rf_dev"; then
        log "repair $_rf_point: device still mounted or mount state unknown -> BLOCKED"; exit 1
    fi
    _rf_output=$("$FSCK" -f -y "$_rf_dev" 2>&1)
    _rf_rc=$?
    log "repair $_rf_point: e2fsck rc=$_rf_rc tail=$(printf '%s' "$_rf_output" | tail -1)"
    case "$_rf_rc" in
        0|1) ;;
        2|3)
            set_prop z1stage.fsck reboot-required
            log "repair $_rf_point: reboot required; left unmounted, no RW mount"
            exit 2 ;;
        *) log "repair $_rf_point: check failed; left unmounted, no RW mount"; exit 1 ;;
    esac
    if ! "$MOUNT" -o rw,seclabel,nosuid,nodev,noatime "$_rf_dev" "$_rf_point"; then
        log "repair $_rf_point: RW mount failed -> BLOCKED"; exit 1
    fi
    _rf_record=$(mount_record "$_rf_point") || exit 1
    set -- $_rf_record
    [ "$#" -eq 2 ] || exit 1
    _rf_actual=$($READLINK -f "$1" 2>/dev/null) || exit 1
    _rf_expected=$($READLINK -f "$_rf_dev" 2>/dev/null) || exit 1
    [ "$_rf_actual" = "$_rf_expected" ] || exit 1
    case ",$2," in *,rw,*) log "repair $_rf_point: verified RW mount"; exit 0 ;; esac
    log "repair $_rf_point: mount did not become RW -> BLOCKED"
    exit 1
)

prepare_fs() (   # <mountpoint> <blockdev>
    _pf_point=$1
    _pf_dev=$2
    _pf_record=$(mount_record "$_pf_point") || {
        log "$_pf_point: mount table unreadable/ambiguous -> BLOCKED"; exit 1;
    }
    _pf_mounted=absent
    _pf_opts=
    if [ "$_pf_record" != absent ]; then
        set -- $_pf_record
        [ "$#" -eq 2 ] || exit 1
        _pf_actual=$($READLINK -f "$1" 2>/dev/null) || exit 1
        _pf_expected=$($READLINK -f "$_pf_dev" 2>/dev/null) || exit 1
        [ -n "$_pf_expected" ] && [ "$_pf_actual" = "$_pf_expected" ] || {
            log "$_pf_point: mounted from unexpected device -> BLOCKED"; exit 1;
        }
        _pf_mounted=mounted
        _pf_opts=$2
    fi
    fs_needs_repair "$_pf_dev"
    _pf_need=$?
    case "$_pf_need" in
        0) repair_fs "$_pf_point" "$_pf_dev" "$_pf_mounted"; exit $? ;;
        1)
            if [ "$_pf_mounted" = absent ]; then
                repair_fs "$_pf_point" "$_pf_dev" absent; exit $?
            fi
            case ",$_pf_opts," in *,rw,*) exit 0 ;; esac
            log "$_pf_point: unexpectedly read-only -> BLOCKED"; exit 1 ;;
        *) log "$_pf_point: filesystem health unknown -> BLOCKED"; exit 1 ;;
    esac
)

# --storage-only belongs immediately after mount_all in on fs.  The later
# post-fs-data invocation uses --skip-storage so fsck never races Android writers.
case "${1:-}" in
    ''|--storage-only)
        set_checked_prop sys.z1.storage_ready 0 || exit 1
        set_prop z1stage.status checking-filesystems
        set_prop z1stage.fsck checking
        for _fs_pair in /data:/dev/block/mmcblk0p6 /cache:/dev/block/mmcblk0p5; do
            prepare_fs "${_fs_pair%%:*}" "${_fs_pair#*:}"
            _fs_rc=$?
            if [ "$_fs_rc" -ne 0 ]; then
                [ "$_fs_rc" -eq 2 ] || set_prop z1stage.fsck failed
                set_prop z1stage.status blocked-filesystem
                log "BLOCKED: filesystem gate failed ($_fs_pair, rc=$_fs_rc); no stage/system writes"
                exit 1
            fi
        done
        set_prop z1stage.fsck ready
        set_checked_prop sys.z1.storage_ready 1 || exit 1
        if [ "${1:-}" = --storage-only ]; then
            set_prop z1stage.status storage-ready
            log "storage gate ready; no stage/system writes in --storage-only"
            exit 0
        fi ;;
    --skip-storage)
        if [ "$("$GETPROP" sys.z1.storage_ready 2>/dev/null)" != 1 ]; then
            set_prop z1stage.status blocked-filesystem
            log "BLOCKED: --skip-storage requires successful early storage gate"
            exit 1
        fi
        for _fs_pair in /data:/dev/block/mmcblk0p6 /cache:/dev/block/mmcblk0p5; do
            _fs_record=$(mount_record "${_fs_pair%%:*}") || _fs_record=unknown
            _fs_source=${_fs_record%% *}
            _fs_opts=${_fs_record#* }
            _fs_actual=$($READLINK -f "$_fs_source" 2>/dev/null)
            _fs_expected=$($READLINK -f "${_fs_pair#*:}" 2>/dev/null)
            case ",$_fs_opts," in *,rw,*) _fs_rw=yes ;; *) _fs_rw=no ;; esac
            if [ -z "$_fs_actual" ] || [ "$_fs_actual" != "$_fs_expected" ] || [ "$_fs_rw" != yes ]; then
                set_prop z1stage.status blocked-filesystem
                log "BLOCKED: mount changed after early storage gate ($_fs_pair)"
                exit 1
            fi
        done ;;
    *) log "usage: z1stage_boot.sh [--storage-only|--skip-storage]"; exit 2 ;;
esac
set_prop z1stage.status installing

# --- 1. stage directory ----------------------------------------------------
if ! $MKDIR -p "$STAGE" "$STAGE/active" 2>/dev/null ||
   ! [ -d "$STAGE" ] || ! [ -d "$STAGE/active" ] ||
   ! $CHMOD 0777 "$STAGE" "$STAGE/active" 2>/dev/null ||
   ! $CHOWN "$SHELL_UID:$SHELL_GID" "$STAGE" "$STAGE/active" 2>/dev/null ||
   ! [ -w "$STAGE" ]; then
    set_prop z1stage.status blocked-stage
    log "BLOCKED: cannot create or configure writable stage ${STAGE}"
    exit 1
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
SYSTEM_WRITE_FAILED=0

system_is_rw() {
    _sr_record=$(mount_record /system) || return 1
    case "$_sr_record" in absent) return 1 ;; esac
    _sr_opts=${_sr_record#* }
    case ",$_sr_opts," in *,rw,*) return 0 ;; esac
    return 1
}

ensure_system_rw() {
    [ "$SYSTEM_WRITE_FAILED" -eq 0 ] || return 1
    if ! system_is_rw; then
        if ! "$MOUNT" -o remount,rw /system 2>/dev/null || ! system_is_rw; then
            log "ERROR /system RW remount failed"
            SYSTEM_WRITE_FAILED=1
            ERRORS=$((ERRORS + 1))
            return 1
        fi
    fi
    return 0
}

restore_system_ro() {
    # Even the legacy fstab initially mounts /system RW.  End installation with
    # it RO, including when no file changed or a copy/verification failed.
    _rr_record=$(mount_record /system) || {
        log "ERROR cannot verify /system mount while restoring RO"; return 1;
    }
    [ "$_rr_record" != absent ] || { log "ERROR /system is not mounted"; return 1; }
    if system_is_rw; then
        "$MOUNT" -o remount,ro /system 2>/dev/null || {
            log "ERROR /system RO remount failed"; return 1;
        }
    fi
    _rr_record=$(mount_record /system) || return 1
    _rr_opts=${_rr_record#* }
    case ",$_rr_opts," in
        *,ro,*) return 0 ;;
        *) log "ERROR /system did not become RO"; return 1 ;;
    esac
}

install_error() {
    ERRORS=$((ERRORS + 1))
    SYSTEM_WRITE_FAILED=1
    log "ERROR $*"
    restore_system_ro || log "ERROR /system remains unsafe after failed install"
    return 1
}

atomic_install() {   # <src> <dst> <label>; cmp errors are not mismatches
    _ai_src=$1
    _ai_dst=$2
    _ai_label=$3
    _ai_tmp="${_ai_dst}.z1tmp.$$"
    [ "$SYSTEM_WRITE_FAILED" -eq 0 ] || return 1
    [ -x "$CMP" ] || { install_error "$_ai_label: cmp unavailable"; return 1; }
    if [ -e "$_ai_dst" ]; then
        "$CMP" -s "$_ai_src" "$_ai_dst" 2>/dev/null
        _ai_cmp=$?
        case "$_ai_cmp" in
            0) log "install $_ai_label: identical; kept existing file"; return 0 ;;
            1) ;;
            *) install_error "$_ai_label: cannot compare existing file"; return 1 ;;
        esac
    fi
    ensure_system_rw || { restore_system_ro; return 1; }
    if ! "$RM" -f "$_ai_tmp" 2>/dev/null ||
       ! "$CP" "$_ai_src" "$_ai_tmp" 2>/dev/null ||
       ! "$CMP" -s "$_ai_src" "$_ai_tmp" 2>/dev/null ||
       ! "$CHMOD" 0644 "$_ai_tmp" 2>/dev/null ||
       ! "$CHOWN" 0:0 "$_ai_tmp" 2>/dev/null; then
        "$RM" -f "$_ai_tmp" 2>/dev/null
        install_error "$_ai_label: temporary copy/verification/metadata failed; destination kept"
        return 1
    fi
    if ! "$MV" -f "$_ai_tmp" "$_ai_dst" 2>/dev/null; then
        "$RM" -f "$_ai_tmp" 2>/dev/null
        install_error "$_ai_label: rename failed; destination kept"
        return 1
    fi
    if ! "$CMP" -s "$_ai_src" "$_ai_dst" 2>/dev/null; then
        install_error "$_ai_label: destination readback failed after rename"
        return 1
    fi
    APPLIED=$((APPLIED + 1))
    log "install $_ai_label: $_ai_src -> $_ai_dst (sha256 $(sha_of "$_ai_dst"))"
    return 0
}

remove_override() {   # <dst> <label>
    [ -e "$1" ] || return 0
    ensure_system_rw || { restore_system_ro; return 1; }
    if "$RM" -f "$1" 2>/dev/null; then
        REMOVED=$((REMOVED + 1))
        log "install $2: override removed; system module takes over"
        return 0
    fi
    install_error "$2: cannot remove override"
    return 1
}

install_one() {   # <file-name> <disable-token>
    _io_name=$1
    _io_active="$STAGE/active/$_io_name"
    _io_dst="$VENDOR_HW/$_io_name"
    [ "$SYSTEM_WRITE_FAILED" -eq 0 ] || return 1
    if [ -f "$STAGE/.disable_$2" ]; then
        remove_override "$_io_dst" "$_io_name (kill switch)"
        return $?
    fi
    if [ -f "$_io_active" ]; then
        _io_src=$_io_active
    elif [ -f "/$_io_name" ]; then
        _io_src="/$_io_name"
    else
        remove_override "$_io_dst" "$_io_name (no candidate)"
        return $?
    fi
    atomic_install "$_io_src" "$_io_dst" "$_io_name"
}

install_sys_file() {   # <src-in-ramdisk> <dst>; absence preserves system image
    [ -f "/$1" ] || return 0
    atomic_install "/$1" "$2" "$1"
}

publish_identity() {
    for _id_module in hwcomposer gralloc; do
        _id_path="$VENDOR_HW/$_id_module.z1.so"
        if [ -f "$_id_path" ]; then
            set_prop "z1stage.${_id_module}.sha" "$(sha_of "$_id_path")"
        else
            set_prop "z1stage.${_id_module}.sha" "system:$(sha_of "/system/lib/hw/$_id_module.z1.so")"
        fi
    done
    log "identity: hwcomposer=$($GETPROP z1stage.hwcomposer.sha 2>/dev/null) gralloc=$($GETPROP z1stage.gralloc.sha 2>/dev/null)"
}

# Best-effort RO restoration also covers an interrupted installation.  Rename
# avoids truncating a mapped library; it is not a power-loss durability proof.
trap 'restore_system_ro; exit 1' HUP INT TERM
install_one hwcomposer.z1.so hwcomposer
install_one gralloc.z1.so gralloc
install_sys_file z1_libwifi-hal.so          /system/vendor/lib/libwifi-hal.so
install_sys_file z1_libbt-vendor.so         /system/vendor/lib/libbt-vendor.so
install_sys_file z1_audio.primary.mt6572.so /system/vendor/lib/hw/audio.primary.mt6572.so
install_sys_file z1_mt6572-afe-pcm.ko        /system/lib/modules/mt6572-afe-pcm.ko
install_sys_file z1_snd-soc-mt6323.ko        /system/lib/modules/snd-soc-mt6323.ko
if ! restore_system_ro; then
    ERRORS=$((ERRORS + 1))
    SYSTEM_WRITE_FAILED=1
fi
trap - HUP INT TERM
if [ "$SYSTEM_WRITE_FAILED" -ne 0 ]; then
    publish_identity
    set_prop z1stage.status blocked-install
    log "BLOCKED: installation failed; no further stage writes"
    exit 1
fi

# --- 2d. smoothness: remove boot-time overhead and framework stalls ---------
# (a) /d and the debugfs wakelock file.  Android's BatteryStatsImpl calls
#     Slog.wtf() -- which *kills* the process on userdebug builds -- when
#     neither /proc/wakelocks nor /d/wakeup_sources resolves.  The kernel has
#     no /proc/wakelocks, and /d is only created once init has mounted
#     debugfs, so system_server can hit the window and die (that is what makes
#     SystemUI ANR/restart "for no reason").  Create and open it up here.
[ -e /d ] || ln -s /sys/kernel/debug /d 2>/dev/null
if [ -e /sys/kernel/debug/wakeup_sources ]; then
    chmod 0644 /sys/kernel/debug/wakeup_sources 2>/dev/null
    log "smooth: /d/wakeup_sources ready ($(ls -l /sys/kernel/debug/wakeup_sources 2>/dev/null | cut -d' ' -f1))"
else
    log "smooth: /sys/kernel/debug/wakeup_sources missing"
fi

# (b) The diagnostic relays are pure overhead while the user is using the
#     device: measured 323 logcat lines/minute relayed to the serial port, plus
#     a dumpsys window/activity/ps/meminfo sweep every relay cycle.  They exist
#     for bring-up; stop them here and re-enable by hand when debugging:
#         setprop ctl.start z1_logcat_relay
for _svc in z1_logcat_relay z1_acm_log z1_logpersist z1_heartbeat z1_sf_dump \
            z1_klog_relay z1_diag_onboot z1selftest; do
    /system/bin/setprop ctl.stop "$_svc" 2>/dev/null
done
log "smooth: diagnostic relays stopped"

# (c) Halve the window/transition/animator scales.  This is the single largest
#     *perceived* win on a 1 GB Cortex-A7: every transition costs ~half the
#     frames.  Applied after the settings provider is up, hence the background
#     wait; harmless if it never runs.
( _i=0
  while [ $_i -lt 240 ]; do
      if /system/bin/getprop sys.boot_completed 2>/dev/null | grep -qx 1; then
          /system/bin/settings put global window_animation_scale 0.5 2>/dev/null
          /system/bin/settings put global transition_animation_scale 0.5 2>/dev/null
          /system/bin/settings put global animator_duration_scale 0.5 2>/dev/null
          break
      fi
      sleep 2; _i=$((_i + 1))
  done ) >/dev/null 2>&1 &


# --- 2c. CPU / GPU frequency policy ----------------------------------------
# Apply only the validated OPP set.  These are the current supported settings,
# not a claim about the silicon's physical upper limit.
CPUF=/sys/devices/system/cpu/cpu0/cpufreq
OC_LEVEL="$STAGE/oc_level"
OC_PENDING="$STAGE/.oc_pending"
if [ -f "$OC_LEVEL" ]; then
    _freq_request=$(cat "$OC_LEVEL" 2>/dev/null)
    if [ "$_freq_request" != safe ]; then
        log "cpufreq: unsupported oc_level=${_freq_request}; requested safe policy"
        printf 'safe\n' > "$OC_LEVEL" 2>/dev/null || log "cpufreq: cannot clear old oc_level request"
    fi
fi
$RM -f "$OC_PENDING" 2>/dev/null || log "cpufreq: cannot remove stale overclock marker"
if [ -d "$CPUF" ]; then
    if grep -qw 806000 "$CPUF/scaling_available_frequencies" 2>/dev/null &&
       grep -qw 1300000 "$CPUF/scaling_available_frequencies" 2>/dev/null &&
       grep -qw schedutil "$CPUF/scaling_available_governors" 2>/dev/null; then
        if printf '1300000\n' > "$CPUF/scaling_max_freq" 2>/dev/null &&
           printf '806000\n' > "$CPUF/scaling_min_freq" 2>/dev/null &&
           printf 'schedutil\n' > "$CPUF/scaling_governor" 2>/dev/null &&
           [ "$(cat "$CPUF/scaling_max_freq" 2>/dev/null)" = 1300000 ] &&
           [ "$(cat "$CPUF/scaling_min_freq" 2>/dev/null)" = 806000 ] &&
           [ "$(cat "$CPUF/scaling_governor" 2>/dev/null)" = schedutil ]; then
            set_prop z1stage.cpufreq ready
            for _rl in /sys/devices/system/cpu/cpufreq/policy0/schedutil/rate_limit_us \
                       /sys/devices/system/cpu/cpufreq/schedutil/rate_limit_us; do
                if [ -w "$_rl" ]; then
                    if printf '2000\n' > "$_rl" 2>/dev/null &&
                       [ "$(cat "$_rl" 2>/dev/null)" = 2000 ]; then
                        log "cpufreq: schedutil rate_limit_us=2000 verified"
                    else
                        log "cpufreq: rate_limit_us write/readback failed"
                    fi
                    break
                fi
            done
        else
            set_prop z1stage.cpufreq failed
            log "cpufreq: policy write/readback failed; actual values follow"
        fi
    else
        set_prop z1stage.cpufreq unsupported
        log "cpufreq: current driver does not advertise 806000/1300000/schedutil; unchanged"
    fi
    log "cpufreq: driver=$(cat "$CPUF/scaling_driver" 2>/dev/null) ceiling=$(cat "$CPUF/scaling_max_freq" 2>/dev/null) floor=$(cat "$CPUF/scaling_min_freq" 2>/dev/null) gov=$(cat "$CPUF/scaling_governor" 2>/dev/null) cur=$(cat "$CPUF/scaling_cur_freq" 2>/dev/null) avail=$(cat "$CPUF/scaling_available_frequencies" 2>/dev/null)"
else
    set_prop z1stage.cpufreq unavailable
    log "cpufreq: no cpufreq node"
fi

# Keep the tested GPU performance policy only when 416 MHz and that governor
# are advertised.  A governor write alone does not prove the resulting clock.
GPU_DEVFREQ=/sys/class/devfreq/13010000.gpu
if [ -d "$GPU_DEVFREQ" ]; then
    if grep -qw 416000000 "$GPU_DEVFREQ/available_frequencies" 2>/dev/null &&
       grep -qw performance "$GPU_DEVFREQ/available_governors" 2>/dev/null; then
        if printf '416000000\n' > "$GPU_DEVFREQ/max_freq" 2>/dev/null &&
           [ "$(cat "$GPU_DEVFREQ/max_freq" 2>/dev/null)" = 416000000 ] &&
           printf 'performance\n' > "$GPU_DEVFREQ/governor" 2>/dev/null &&
           [ "$(cat "$GPU_DEVFREQ/governor" 2>/dev/null)" = performance ] &&
           [ "$(cat "$GPU_DEVFREQ/cur_freq" 2>/dev/null)" = 416000000 ]; then
            set_prop z1stage.devfreq ready
        else
            set_prop z1stage.devfreq failed
            log "devfreq: performance/416MHz write or readback failed"
        fi
    else
        set_prop z1stage.devfreq unsupported
        log "devfreq: 416MHz/performance not advertised; unchanged"
    fi
    log "devfreq: governor=$(cat "$GPU_DEVFREQ/governor" 2>/dev/null) cur=$(cat "$GPU_DEVFREQ/cur_freq" 2>/dev/null) avail=$(cat "$GPU_DEVFREQ/available_frequencies" 2>/dev/null)"
else
    set_prop z1stage.devfreq unavailable
    log "devfreq: GPU node absent"
fi

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



# --- 3b. actual installed identity (including operator overrides) ------------
publish_identity

T1=$(uptime_s)
log "done @${T1}s: seeded=${SEEDED} working_copies=${COPIED} applied=${APPLIED} removed=${REMOVED} errors=${ERRORS}"
if [ "$ERRORS" -ne 0 ]; then
    set_prop z1stage.status completed-with-errors
    exit 1
fi
set_checked_prop z1stage.status ready || exit 1
exit 0

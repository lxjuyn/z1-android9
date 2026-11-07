#!/system/bin/sh
# =============================================================================
# z1_fsck_guard.sh  --  Z1 (MT6572, Android 9 / Pie) ext4 self-heal guard
#
# Placed in the boot ramdisk as /z1_fsck_guard.sh.
#
# WHY THIS EXISTS
#   2026-11-05 boot: /data (mmcblk0p6) hit
#       EXT4-fs error (device mmcblk0p6): ext4_free_inode:354:
#         comm re-initialized>: bit already cleared for inode 7743
#     -> Aborting journal -> Remounting filesystem read-only
#   Once /data is RO, app_process's maybeCreateDalvikCache() chown of
#   /data/dalvik-cache/<abi> fails with EROFS and LOG_ALWAYS_FATAL makes
#   zygote SIGABRT every ~5 s.  init restarts it forever and the screen
#   stays on the boot animation.
#
# WHAT IT DOES
#   --pre   (default): runs from init's "on fs", BEFORE mount_all, and only
#           when the ext4 superblock says the fs was not cleanly unmounted,
#           carries the EXT4_ERROR_FS flag, or is due by mount count.
#           It then runs e2fsck -f -y on the unmounted device.
#   --post  : runs from "on post-fs-data" and verifies that /data really is
#           mounted read-write (write test).  If it is RO it reports it and
#           drops a marker so the next boot force-check's the fs.
#   --status: print the verdict, change nothing.
#
# GUARANTEES
#   * Never exits nonzero for --pre / --post.  A broken fsck must not stop
#     the boot; fs_mgr will still try to mount, and if that fails Android
#     keeps booting with the ramdisk's volatile /data directory.
#   * Prints exactly one machine-readable RESULT line to /dev/kmsg:
#       [z1fsck] <dev> RESULT=<CLEAN|FSCKED|FAILED|SKIP|NO_DEV> ...
#
# USAGE (pre):
#   z1_fsck_guard.sh [--pre] [--dev /dev/block/mmcblk0p6] [--force]
#                    [--degrade-fstab /fstab.z1] [--kmsg /dev/kmsg] [--e2fsck ...]
# USAGE (post):
#   z1_fsck_guard.sh --post [--kmsg /dev/kmsg] [--mountpoint /data]
# =============================================================================

DEV=/dev/block/mmcblk0p6          # /data
E2FSCK=/system/bin/e2fsck
KMSG=/dev/kmsg
MOUNTPOINT=/data
WAIT_SECONDS=20
FSCK_TIMEOUT=300
LOG=/z1_fsck_last.log
MODE=pre
FORCE=0
DEGRADE_FSTAB=""
FSTAB_DEV=""                     # device string to match in --degrade-fstab

EXT4_MAGIC=61267                  # 0xEF53
EXT4_VALID_FS=0x0001              # s_state: fs cleanly unmounted
EXT4_ERROR_FS=0x0002              # s_state: kernel recorded an error
EXT4_INCOMPAT_RECOVER=0x0004      # s_feature_incompat: journal needs replay
EXT4_INCOMPAT_64BIT=0x0080

while [ $# -gt 0 ]; do
    case "$1" in
        --pre)     MODE=pre ;;
        --post)    MODE=post ;;
        --status)  MODE=status ;;
        --force)   FORCE=1 ;;
        --dev)     shift; DEV="$1" ;;
        --e2fsck)  shift; E2FSCK="$1" ;;
        --kmsg)    shift; KMSG="$1" ;;
        --mountpoint) shift; MOUNTPOINT="$1" ;;
        --log)     shift; LOG="$1" ;;
        --wait-seconds) shift; WAIT_SECONDS="$1" ;;
        --degrade-fstab) shift; DEGRADE_FSTAB="$1" ;;
        --fstab-device) shift; FSTAB_DEV="$1" ;;
        -h|--help) sed -n '2,40p' "$0"; exit 0 ;;
        *)         : ;;
    esac
    shift
done

# --- logging ---------------------------------------------------------------
if [ "$KMSG" = /dev/kmsg ]; then
    kmsg() { printf '%s\n' "$*" > /dev/kmsg 2>/dev/null || true; }
else
    # test/host mode: append to a normal file (created on demand)
    kmsg() { printf '%s\n' "$*" >> "$KMSG" 2>/dev/null || printf '%s\n' "$*" >&2; }
fi

# --- little-endian reader: read_le <byte-offset-in-superblock> <1..4> -------
# No awk dependency: this image's toybox does not ship awk.  od -An -tu1
# prints the bytes as decimal words; the shell does the little-endian math.
read_le() {
    _off=$1
    _cnt=$2
    _bytes=$(dd if="$DEV" bs=1 skip=$((1024 + _off)) count="$_cnt" 2>/dev/null |
             od -An -tu1 -v 2>/dev/null)
    _v=0
    _m=1
    for _b in $_bytes; do
        _v=$((_v + _b * _m))
        _m=$((_m * 256))
    done
    printf '%s' "$_v"
}

hex2() { printf '0x%04x' "$1" 2>/dev/null || printf '0x%04x' 0; }
hex4() { printf '0x%08x' "$1" 2>/dev/null || printf '0x%08x' 0; }

initvals() {
    MAGIC=$(read_le 56 2)          # s_magic       @ 0x38
    STATE=$(read_le 58 2)          # s_state       @ 0x3A
    ERRORS=$(read_le 60 2)         # s_errors      @ 0x3C
    MNT=$(read_le 52 2)            # s_mnt_count   @ 0x34
    MAXMNT=$(read_le 54 2)         # s_max_mnt_count @0x36
    INCOMPAT=$(read_le 96 4)       # s_feature_incompat @0x60
    ROCOMPAT=$(read_le 100 4)      # s_feature_ro_compat@0x64
    LASTCHECK=$(read_le 64 4)      # s_lastcheck   @ 0x40
    CHECKINT=$(read_le 68 4)       # s_checkinterval @0x44
}

# --- build the one-line verdict -------------------------------------------
# Sets UNNEEDED/REASON/NEED.
decide() {
    NEED=0
    REASON=""
    RECOVER=0
    [ $((INCOMPAT & EXT4_INCOMPAT_RECOVER)) -ne 0 ] && RECOVER=1

    if [ $((STATE & EXT4_VALID_FS)) -eq 0 ]; then
        NEED=1; REASON="$REASON s_state!VALID"
    fi
    if [ $((STATE & EXT4_ERROR_FS)) -ne 0 ]; then
        NEED=1; REASON="$REASON EXT4_ERROR_FS"
    fi
    if [ "$RECOVER" = 1 ]; then
        NEED=1; REASON="$REASON journal-needs-recovery"
    fi
    # s_max_mnt_count == -1 (0xffff) or 0 means "never force by count"
    if [ "$MAXMNT" -ne 0 ] && [ "$MAXMNT" -ne 65535 ] && [ "$MNT" -ge "$MAXMNT" ]; then
        NEED=1; REASON="$REASON mount-count-due($MNT/$MAXMNT)"
    fi
    # time-based interval (normally 0 = disabled)
    if [ "$CHECKINT" -gt 0 ] && [ "$LASTCHECK" -gt 0 ]; then
        NOW=$(date +%s 2>/dev/null || echo 0)
        if [ "$NOW" -gt 0 ] && [ $((NOW - LASTCHECK)) -ge "$CHECKINT" ]; then
            NEED=1; REASON="$REASON check-interval-due"
        fi
    fi
    if [ "$FORCE" = 1 ]; then
        NEED=1; REASON="$REASON forced"
    fi
}

# --- pre-mount mode --------------------------------------------------------
run_pre() {
    _i=0
    while [ ! -e "$DEV" ] && [ $_i -lt "$WAIT_SECONDS" ]; do
        sleep 1; _i=$((_i + 1))
    done
    if [ ! -e "$DEV" ]; then
        kmsg "<3>[z1fsck] $DEV RESULT=NO_DEV waited=${WAIT_SECONDS}s"
        return 0
    fi

    initvals
    if [ "$MAGIC" != "$EXT4_MAGIC" ]; then
        kmsg "<3>[z1fsck] $DEV RESULT=SKIP magic=$(hex2 "$MAGIC") (not ext4; fs_mgr decides)"
        return 0
    fi
    if [ $((INCOMPAT & EXT4_INCOMPAT_64BIT)) -ne 0 ]; then
        kmsg "<6>[z1fsck] $DEV NOTE=64bit-incompat (this kernel read-only mounts it? 3.4-era code warns)"
    fi

    decide
    if [ "$NEED" != 1 ]; then
        kmsg "<6>[z1fsck] $DEV RESULT=CLEAN state=$(hex2 "$STATE") incompat=$(hex4 "$INCOMPAT") mnt=${MNT}/${MAXMNT} no-fsck-needed"
        return 0
    fi

    if [ ! -x "$E2FSCK" ]; then
        kmsg "<3>[z1fsck] $DEV RESULT=SKIP reason=${REASON# } but $E2FSCK missing"
        return 0
    fi

    kmsg "<6>[z1fsck] $DEV NEEDED reason=${REASON# } state=$(hex2 "$STATE") incompat=$(hex4 "$INCOMPAT") mnt=${MNT}/${MAXMNT} running e2fsck -f -y"

    # If LOG cannot be created (read-only /), do not let a failed redirection
    # stop e2fsck from running at all.
    if : > "$LOG" 2>/dev/null; then
        _out="$LOG"
    else
        _out=/dev/null
    fi
    if command -v timeout >/dev/null 2>&1; then
        timeout "$FSCK_TIMEOUT" "$E2FSCK" -f -y -C 0 "$DEV" > "$_out" 2>&1
    else
        "$E2FSCK" -f -y -C 0 "$DEV" > "$_out" 2>&1
    fi
    rc=$?

    case "$rc" in
        0)  verdict=CLEAN ;;
        1)  verdict=FSCKED ;;
        2)  verdict=FSCKED_REBOOT_NEEDED ;;
        4)  verdict=FAILED_UNCORRECTED ;;
        8)  verdict=FAILED_OPERATIONAL ;;
        16) verdict=FAILED_USAGE ;;
        32) verdict=CANCELLED ;;
        *)  verdict="FAILED_RC_$rc" ;;
    esac

    # Surface the lines that matter (repairs / corruption) without flooding kmsg
    if [ -f "$LOG" ]; then
        grep -E "Fix\\?|corrupt|Multiply|bitmap|count wrong|Clear|Salvage|Pass [1-5]" "$LOG" 2>/dev/null |
            head -n 8 |
            while IFS= read -r _l; do
                kmsg "<6>[z1fsck] e2fsck: $_l"
            done
    fi

    if [ "$rc" -ge 4 ] || [ "$rc" = 8 ] || [ "$rc" = 16 ] || [ "$rc" = 32 ]; then
        # Last-resort degradation: an unmountable /data makes fs_mgr_mount_all()
        # return FAIL, which means init never sets ro.crypto.state and never
        # queues "nonencrypted", so zygote (class main) is never started and the
        # screen freezes on the boot animation with no diagnosis.  Dropping the
        # /data line from the (tmpfs) fstab lets mount_all succeed, so Android
        # boots on the ramdisk's volatile /data directory instead.
        if [ -n "$DEGRADE_FSTAB" ] && [ -f "$DEGRADE_FSTAB" ]; then
            _fdev=${FSTAB_DEV:-$DEV}
            cp "$DEGRADE_FSTAB" "$DEGRADE_FSTAB.z1fsck.bak" 2>/dev/null || true
            : > "$DEGRADE_FSTAB.new" 2>/dev/null || true
            _hit=0
            while IFS= read -r _line; do
                case "$_line" in
                    "$_fdev"*)
                        printf '#Z1FSCK_DEGRADED %s\n' "$_line" >> "$DEGRADE_FSTAB.new"
                        _hit=1 ;;
                    *)  printf '%s\n' "$_line" >> "$DEGRADE_FSTAB.new" ;;
                esac
            done < "$DEGRADE_FSTAB"
            if [ "$_hit" = 1 ]; then
                cat "$DEGRADE_FSTAB.new" > "$DEGRADE_FSTAB" 2>/dev/null || true
                kmsg "<3>[z1fsck] DEGRADED: /data entry commented out in $DEGRADE_FSTAB (boot continues with volatile /data; backup $DEGRADE_FSTAB.z1fsck.bak)"
            fi
            rm -f "$DEGRADE_FSTAB.new" 2>/dev/null || true
        fi
        kmsg "<3>[z1fsck] $DEV RESULT=$verdict rc=$rc log=$LOG -- fs_mgr will still try to mount; if it fails zygote never starts (ro.crypto.state unset)"
        command -v setprop >/dev/null 2>&1 && setprop z1.data.fsck_failed 1
    elif [ "$rc" = 2 ]; then
        kmsg "<6>[z1fsck] $DEV RESULT=$verdict rc=$rc log=$LOG -- errors repaired (e2fsck asks for a reboot; mount_all continues)"
        command -v setprop >/dev/null 2>&1 && setprop z1.data.fsck_failed 0
    else
        kmsg "<6>[z1fsck] $DEV RESULT=$verdict rc=$rc log=$LOG -- fs is clean for mount_all"
        command -v setprop >/dev/null 2>&1 && setprop z1.data.fsck_failed 0
    fi
    return 0
}

# --- post-mount verification ----------------------------------------------
run_post() {
    _mntline=""
    while read -r _src _mp _type _opts _rest; do
        if [ "$_mp" = "$MOUNTPOINT" ]; then
            _mntline="$_src $_type $_opts"
            break
        fi
    done < /proc/mounts 2>/dev/null
    if [ -z "$_mntline" ]; then
        kmsg "<3>[z1fsck] POST $MOUNTPOINT RESULT=NOT_MOUNTED -- Android will boot with the ramdisk's volatile directory"
        command -v setprop >/dev/null 2>&1 && setprop z1.data.state notmounted
        return 0
    fi

    set -- $_mntline
    _mntsrc=$1
    _mntopts=$3
    _ro=0
    case ",$_mntopts," in
        *,ro,*) _ro=1 ;;
    esac

    if touch "$MOUNTPOINT/.z1_rwtest" 2>/dev/null; then
        rm -f "$MOUNTPOINT/.z1_rwtest" 2>/dev/null
        _rw=1
    else
        _rw=0
    fi

    if [ "$_ro" = 1 ] || [ "$_rw" = 0 ]; then
        kmsg "<3>[z1fsck] POST $MOUNTPOINT RESULT=READ_ONLY src=$_mntsrc opts=$_mntopts ro=${_ro} wtest=${_rw} -- zygote will SIGABRT on /data/dalvik-cache chown"
        command -v setprop >/dev/null 2>&1 && setprop z1.data.state readonly
        # leave a marker the pre-mount pass can see next boot (best effort;
        # /cache is usually mounted rw at this point)
        [ -d /cache ] && { : > /cache/z1_fsck_request 2>/dev/null || true; }
    else
        kmsg "<6>[z1fsck] POST $MOUNTPOINT RESULT=OK src=$_mntsrc ro=0 wtest=1"
        command -v setprop >/dev/null 2>&1 && setprop z1.data.state ok
        rm -f /cache/z1_fsck_request 2>/dev/null || true
    fi
    return 0
}

case "$MODE" in
    pre)    run_pre ;;
    post)   run_post ;;
    status)
        initvals
        decide
        echo "$DEV magic=$(hex2 "$MAGIC") state=$(hex2 "$STATE") errors=$(hex2 "$ERRORS") incompat=$(hex4 "$INCOMPAT") ro_compat=$(hex4 "$ROCOMPAT") mnt=${MNT}/${MAXMNT} lastcheck=${LASTCHECK} interval=${CHECKINT} need_fsck=${NEED} reason=${REASON# }"
        ;;
esac
exit 0

#!/usr/bin/env bash
# =============================================================================
#  z1_extract_devlibs.sh — rebuild a "device libraries" directory from the Z1
#  Android 9 system image, so out-of-tree HALs can be linked against the *exact*
#  libraries that run on the device (armv7, bionic, clang 17.0.2).
#
#  The image is loop-mounted READ-ONLY (nothing is ever written to it), the 11
#  libs below are copied, and every copy is verified with `file` (must be an
#  ELF 32-bit ARM object) plus size > 0 plus sha256(src) == sha256(dst).
#  Re-running is idempotent: unchanged files are reported, not recopied.
#
#  Usage
#    ./z1_extract_devlibs.sh [outdir]      default outdir: /tmp/z1devlibs
#    ./z1_extract_devlibs.sh --help
#
#  Environment overrides
#    IMG         system image   (default: <repo>/out/z1_v35_selftest/system_v35.img)
#    MNT         mount point    (default: /mnt/z1sys)
#    SUDO_PASS   sudo password  (default: 1; only used if sudo asks for one)
#    Z1_SYS_IMAGE / Z1_SYS_MNT  aliases for IMG / MNT
#
#  If MNT is already a mount point the existing mount is reused (and left alone,
#  i.e. not unmounted on exit).
# =============================================================================
set -eu
export LC_ALL=C

# The exact set of libraries that the Z1 device carries in /system/lib and that
# out-of-tree HAL work needs.
Z1_LIBS="libc++.so libcutils.so liblog.so libutils.so libsync.so libhardware.so libui.so libgui.so libEGL.so libGLESv2.so libbinder.so"

# ---------------------------------------------------------------------------
# 0. self-location
# ---------------------------------------------------------------------------
_self=${BASH_SOURCE[0]:-$0}
while [ -L "$_self" ]; do
    _dir=$(cd -P -- "$(dirname -- "$_self")" && pwd)
    _self=$(readlink -- "$_self")
    case $_self in
        /*) ;;
        *) _self=$_dir/$_self ;;
    esac
done
SCRIPT_DIR=$(cd -P -- "$(dirname -- "$_self")" && pwd)
SCRIPT_NAME=$(basename -- "$_self")
REPO_ROOT=$(cd -P -- "$SCRIPT_DIR/../.." && pwd)

# ---------------------------------------------------------------------------
# 1. helpers
# ---------------------------------------------------------------------------
info() { printf '[*] %s\n' "$*"; }
warn() { printf '[!] %s\n' "$*" >&2; }
die() {
    printf '\n[x] ERROR: %s\n' "$*" >&2
    exit 1
}
usage() {
    cat <<EOF
Usage: $SCRIPT_NAME [outdir]
       $SCRIPT_NAME --help

Copy the Z1 device runtime libraries out of the Android 9 system image into
<outdir> (default /tmp/z1devlibs), then build with:

    DEVICE_LIBS=<outdir> $SCRIPT_DIR/z1hwc2/build.sh

Environment:
    IMG=$IMG
    MNT=$MNT
    SUDO_PASS=${SUDO_PASS:-(unset -> 1)}

Libraries copied (all must be present and be ELF 32-bit ARM):
    $Z1_LIBS

The image is mounted read-only and is unmounted again on exit; an already
existing mount at MNT is reused and left untouched.
EOF
}
is_mountpoint() {
    if command -v mountpoint >/dev/null 2>&1; then
        mountpoint -q -- "$1"
        return
    fi
    awk -v m="$1" '$2 == m { f = 1 } END { exit f ? 0 : 1 }' /proc/mounts
}
sha_of() { sha256sum -- "$1" 2>/dev/null | awk '{print $1}'; }

# ---------------------------------------------------------------------------
# 2. argv / configuration
# ---------------------------------------------------------------------------
case ${1:-} in
    -h | --help)
        IMG=${IMG:-${Z1_SYS_IMAGE:-$REPO_ROOT/out/z1_v35_selftest/system_v35.img}}
        MNT=${MNT:-${Z1_SYS_MNT:-/mnt/z1sys}}
        usage
        exit 0
        ;;
    -*)
        die "unknown option: $1 (try --help)"
        ;;
esac
if [ "$#" -gt 1 ]; then
    die "too many arguments (try --help)"
fi

IMG=${IMG:-${Z1_SYS_IMAGE:-$REPO_ROOT/out/z1_v35_selftest/system_v35.img}}
MNT=${MNT:-${Z1_SYS_MNT:-/mnt/z1sys}}
SUDO_PASS=${SUDO_PASS:-1}
OUTDIR=${1:-${OUTDIR:-/tmp/z1devlibs}}

# ---------------------------------------------------------------------------
# 3. sudo plumbing (read-only mount only; no data is written to the image)
# ---------------------------------------------------------------------------
sudo_run() {
    if [ "$(id -u)" -eq 0 ]; then
        "$@"
    elif sudo -n true 2>/dev/null; then
        sudo -- "$@"
    else
        printf '%s\n' "$SUDO_PASS" | sudo -S -p '' -- "$@"
    fi
}

if [ "$(id -u)" -ne 0 ] && ! command -v sudo >/dev/null 2>&1; then
    die "not root and sudo not available — cannot loop-mount the image"
fi

# ---------------------------------------------------------------------------
# 4. mount the image read-only (reuse an existing mount when possible)
# ---------------------------------------------------------------------------
MOUNTED_BY_US=0
cleanup() {
    local rc=$?
    trap - EXIT INT TERM
    if [ "$MOUNTED_BY_US" -eq 1 ]; then
        if sudo_run umount -- "$MNT" 2>/dev/null; then
            info "unmounted $MNT (was mounted by this script)"
        else
            warn "could not unmount $MNT — unmount it manually: sudo umount $MNT"
        fi
    fi
    exit "$rc"
}
trap cleanup EXIT INT TERM

printf '============================================================\n'
printf ' Z1 device library extraction\n'
printf '============================================================\n'
info "repo root      : $REPO_ROOT"
info "system image   : $IMG"
info "mount point    : $MNT"
info "outdir         : $OUTDIR"

if is_mountpoint "$MNT"; then
    info "reusing the existing mount at $MNT (will not be unmounted)"
    SRC=$MNT/lib
    BACKING=""
    mnt_src=$(findmnt -no SOURCE -- "$MNT" 2>/dev/null || true)
    if [ -n "$mnt_src" ]; then
        bn=$(basename -- "$mnt_src")
        bp=/sys/block/$bn/loop/backing_file
        if [ -r "$bp" ]; then
            BACKING=$(cat -- "$bp" 2>/dev/null || true)
        fi
        if [ -n "$BACKING" ]; then
            info "backing file   : $BACKING"
            if [ "$BACKING" != "$IMG" ]; then
                warn "the mounted image is not IMG=$IMG — using the mounted one"
            fi
        fi
    fi
else
    if [ ! -f "$IMG" ]; then
        die "system image not found: $IMG (set IMG=/path/to/system.img)"
    fi
    if [ ! -s "$IMG" ]; then
        die "system image is empty: $IMG"
    fi
    if [ -e "$MNT" ] && [ ! -d "$MNT" ]; then
        die "mount point exists but is not a directory: $MNT"
    fi
    if [ ! -d "$MNT" ]; then
        if ! sudo_run mkdir -p -- "$MNT"; then
            die "cannot create mount point: $MNT"
        fi
    fi
    info "mounting read-only (loop) ..."
    if ! sudo_run mount -o loop,ro -- "$IMG" "$MNT"; then
        die "mount failed: $IMG -> $MNT"
    fi
    MOUNTED_BY_US=1
    if ! is_mountpoint "$MNT"; then
        die "mount reported success but $MNT is not a mount point"
    fi
    SRC=$MNT/lib
fi

if [ ! -d "$SRC" ]; then
    die "expected library directory not found: $SRC"
fi

# Normalise the outdir without requiring it to exist yet, so the "inside the
# read-only image" guard can run before mkdir.
if command -v realpath >/dev/null 2>&1; then
    OUTDIR=$(realpath -m -- "$OUTDIR")
    MNT_ABS=$(realpath -m -- "$MNT" 2>/dev/null || true)
else
    case $OUTDIR in
        /*) ;;
        *) OUTDIR=$PWD/$OUTDIR ;;
    esac
    MNT_ABS=$MNT
fi

# Refuse to write into the read-only image.
if [ -n "$MNT_ABS" ]; then
    case $OUTDIR/ in
        "$MNT_ABS"/*)
            die "outdir must not live inside the mounted image: $OUTDIR"
            ;;
    esac
fi

if [ -e "$OUTDIR" ] && [ ! -d "$OUTDIR" ]; then
    die "outdir exists but is not a directory: $OUTDIR"
fi
mkdir -p -- "$OUTDIR" || die "cannot create outdir: $OUTDIR"
if [ ! -w "$OUTDIR" ]; then
    die "outdir is not writable: $OUTDIR"
fi
OUTDIR=$(cd -P -- "$OUTDIR" && pwd)

# ---------------------------------------------------------------------------
# 5. copy + verify every library
# ---------------------------------------------------------------------------
# probe_lib <path> -> sets P_SIZE / P_DESC / P_ERR ; returns 0 when the file is
# a non-empty ELF 32-bit ARM object.
P_SIZE=0
P_DESC=""
P_ERR=""
probe_lib() {
    local p=$1
    P_SIZE=0
    P_DESC=""
    P_ERR=""
    if [ ! -e "$p" ]; then
        P_ERR="does not exist"
        return 1
    fi
    if [ ! -f "$p" ]; then
        P_ERR="not a regular file"
        return 1
    fi
    P_SIZE=$(stat -c %s -- "$p" 2>/dev/null || printf '0')
    if [ "${P_SIZE:-0}" -le 0 ]; then
        P_ERR="size is 0"
        return 1
    fi
    P_DESC=$(file -b -- "$p" 2>/dev/null || true)
    case $P_DESC in
        *"ELF 32-bit"*ARM*) ;;
        *)
            P_ERR="not an ELF 32-bit ARM object: ${P_DESC:-<file failed>}"
            return 1
            ;;
    esac
    return 0
}

printf '\n[1/2] copy + verify %d libraries from %s\n' "$(printf '%s\n' $Z1_LIBS | wc -l)" "$SRC"

ROWS=()
copied=0
unchanged=0
failed=0
n=0
for lib in $Z1_LIBS; do
    n=$((n + 1))
    src=$SRC/$lib
    dst=$OUTDIR/$lib
    printf '\n  (%d) %s\n' "$n" "$lib"

    if ! probe_lib "$src"; then
        printf '        SOURCE REJECTED: %s\n' "$P_ERR"
        failed=$((failed + 1))
        ROWS+=("$(printf '%-20s %10s %-16s %s' "$lib" "-" "-" "SOURCE-BAD")")
        continue
    fi
    src_sha=$(sha_of "$src")
    src_size=$P_SIZE
    printf '        file  : %s\n' "$(printf '%s' "$P_DESC" | cut -c1-110)"
    printf '        bytes : %s\n' "$src_size"
    printf '        sha256: %s\n' "$src_sha"

    if [ -f "$dst" ] && [ "$(sha_of "$dst")" = "$src_sha" ]; then
        status="unchanged"
        unchanged=$((unchanged + 1))
    else
        if ! cp -f -- "$src" "$dst"; then
            printf '        COPY FAILED\n'
            failed=$((failed + 1))
            ROWS+=("$(printf '%-20s %10s %-16s %s' "$lib" "$src_size" "${src_sha:0:16}" "COPY-FAIL")")
            continue
        fi
        status="copied"
        copied=$((copied + 1))
    fi

    # verify the destination independently
    if ! probe_lib "$dst"; then
        printf '        DEST REJECTED: %s\n' "$P_ERR"
        failed=$((failed + 1))
        ROWS+=("$(printf '%-20s %10s %-16s %s' "$lib" "$src_size" "${src_sha:0:16}" "DEST-BAD")")
        continue
    fi
    dst_sha=$(sha_of "$dst")
    if [ "$dst_sha" != "$src_sha" ]; then
        printf '        SHA MISMATCH: src=%s dst=%s\n' "$src_sha" "$dst_sha"
        failed=$((failed + 1))
        ROWS+=("$(printf '%-20s %10s %-16s %s' "$lib" "$src_size" "${src_sha:0:16}" "SHA-BAD")")
        continue
    fi
    printf '        verify: ELF32 ARM ok, sha256(src)==sha256(dst) ok'
    if [ "$P_SIZE" != "$src_size" ]; then
        printf ', SIZE DIFFERS (src=%s dst=%s)' "$src_size" "$P_SIZE"
    fi
    printf '\n'
    printf '        status: %s\n' "$status"
    ROWS+=("$(printf '%-20s %10s %-16s %s' "$lib" "$P_SIZE" "${src_sha:0:16}" "$status")")
done

# ---------------------------------------------------------------------------
# 6. summary
# ---------------------------------------------------------------------------
printf '\n[2/2] summary\n'
printf '====================================================================================\n'
printf ' %-20s %10s %-16s %s\n' "LIBRARY" "BYTES" "SHA256(16)" "STATUS"
printf '%s\n' '------------------------------------------------------------------------------------'
for row in "${ROWS[@]}"; do
    printf ' %s\n' "$row"
done
printf '====================================================================================\n'
printf ' outdir  : %s\n' "$OUTDIR"
printf ' result  : %d copied, %d unchanged, %d failed (of %d)\n' \
    "$copied" "$unchanged" "$failed" "$n"

if [ "$failed" -ne 0 ]; then
    printf '\n[x] ERROR: %d of %d device libraries could not be extracted/verified.\n' \
        "$failed" "$n" >&2
    printf '    Fix the image or the mount, then re-run: %s %s\n' \
        "$SCRIPT_NAME" "$OUTDIR" >&2
    exit 1
fi

printf '\nNext step:\n'
printf '    DEVICE_LIBS=%s %s/z1hwc2/build.sh\n' "$OUTDIR" "$SCRIPT_DIR"
exit 0

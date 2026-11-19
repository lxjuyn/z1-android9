#!/system/bin/sh
# Read the five known services.odex pages with O_DIRECT before cache cleanup.

PATH=/system/bin:/system/xbin:/sbin:/bin
export PATH

[ "$(id -u)" = 0 ] || {
    echo "z1_odex_probe: root is required" >&2
    exit 2
}

TARGET=/system/framework/oat/arm/services.odex
HELPER=/system/bin/z1_odex_direct
EXPECTED=${Z1_ODEX_EXPECTED:-60bfb8d0911b1f9a3be899507f4e6cf2e36c44fae4d5413221eacbf77279a49a}

[ -f "$TARGET" ] && [ ! -L "$TARGET" ] || {
    echo "z1_odex_probe: target missing or symlink" >&2
    exit 2
}
[ -x "$HELPER" ] || {
    echo "z1_odex_probe: direct-read helper missing" >&2
    exit 2
}

stamp=$(date +%Y%m%d_%H%M%S 2>/dev/null) ||
    stamp=$(cut -d. -f1 /proc/uptime 2>/dev/null) ||
    stamp=unknown
out=${1:-/data/local/tmp/z1diag/odex-$stamp}
mkdir -p "$out" || exit 2

echo "out=$out"
echo "uptime=$(cat /proc/uptime 2>/dev/null || echo UNAVAILABLE)"
echo "boot_completed=$(getprop sys.boot_completed 2>/dev/null || echo UNAVAILABLE)"
echo "system_server_pid=$(pidof system_server 2>/dev/null || echo absent)"
echo "surfaceflinger_pid=$(pidof surfaceflinger 2>/dev/null || echo absent)"

# The ordinary read includes the page cache. Preserve the board result before
# any cache cleanup so a warm/cold discrepancy remains visible.
if file_sha=$(sha256sum "$TARGET" 2>/dev/null); then
    echo "cached_sha256=${file_sha%% *}" | tee "$out/cached.sha256"
else
    echo "cached_sha256=READ_FAILED" | tee "$out/cached.sha256"
fi

if "$HELPER" > "$out/pages.bin"; then
    direct=$(sha256sum "$out/pages.bin" 2>/dev/null)
    direct=${direct%% *}
    echo "direct_sha256=$direct" | tee "$out/direct.sha256"
    echo "expected_direct_sha256=$EXPECTED" | tee "$out/expected.sha256"
    if [ "$direct" = "$EXPECTED" ]; then
        echo "result=DIRECT_MATCH_IMAGE"
        rc=0
    else
        echo "result=DIRECT_MISMATCH_IMAGE"
        rc=1
    fi
else
    echo "direct_sha256=READ_FAILED"
    echo "result=DIRECT_READ_FAILED"
    rc=1
fi

if dmesg 2>/dev/null |
    grep -iE 'ext4-fs.*(error|warning)|aborting journal|remounting filesystem read-only' |
    tail -40 > "$out/kernel-storage-errors.txt"; then
    count=$(wc -l < "$out/kernel-storage-errors.txt")
    echo "kernel_storage_error_lines=$count"
else
    echo "kernel_storage_error_lines=UNAVAILABLE"
fi

exit "$rc"

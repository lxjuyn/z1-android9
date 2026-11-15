#!/system/bin/sh
# Read-only NVRAM evidence. No service, property, mount or device is changed.
# Exit 1: missing/invalid prerequisite; 2: evidence remains incomplete.
# Contents, addresses and hashes of device calibration records are never printed.
[ "$#" -eq 0 ] || { printf 'FAIL arguments unsupported\n'; exit 1; }

failures=0
unknowns=0
pass() { printf 'PASS %s\n' "$1"; }
fail() { printf 'FAIL %s\n' "$1"; failures=$((failures + 1)); }
unknown() { printf 'UNKNOWN %s\n' "$1"; unknowns=$((unknowns + 1)); }
regular() { [ ! -L "$1" ] && [ -f "$1" ] && [ -r "$1" ]; }

# Inspect at most the ELF header; do not execute an unverified factory binary.
arm_elf() {
    regular "$1" || return 1
    header=$(od -An -v -tx1 -N20 "$1" 2>/dev/null) || return 1
    set -- $header
    [ "$#" -eq 20 ] || return 1
    [ "$1 $2 $3 $4 $5 $6" = '7f 45 4c 46 01 01' ] || return 1
    shift 16
    case "$1 $2" in '02 00'|'03 00') ;; *) return 1 ;; esac
    [ "$3 $4" = '28 00' ]
}

for binary in /system/bin/nvram_daemon \
    /vendor/lib/libnvram.so /vendor/lib/libcustom_nvram.so \
    /vendor/lib/libnvram_sec.so /vendor/lib/libnvram_platform.so \
    /vendor/lib/libhwm.so /vendor/lib/libfile_op.so \
    /vendor/lib/libnvram_daemon_callback.so; do
    if arm_elf "$binary"; then pass "ELF32_ARM $binary"; else fail "ELF32_ARM $binary"; fi
done
if [ -x /system/bin/nvram_daemon ]; then pass daemon_executable; else fail daemon_executable; fi

service_state=$(getprop init.svc.nvram_daemon 2>/dev/null) || service_state=
case "$service_state" in
    running) pass daemon_running_snapshot ;;
    stopped|stopping|restarting) unknown daemon_not_running ;;
    *) fail daemon_service_not_observed ;;
esac
reported=$(getprop nvram_init 2>/dev/null) || reported=
if [ "$reported" = Ready ]; then
    pass daemon_self_reported_ready
else
    unknown daemon_has_not_reported_ready
fi

flag=/data/nvram/RestoreFlag
if regular "$flag"; then
    # Vendor ARM32 compares a little-endian 0x12345678 marker, not text.
    marker=$(od -An -v -tx1 -N4 "$flag" 2>/dev/null) || marker=
    set -- $marker
    if [ "$#" -eq 4 ] && [ "$1 $2 $3 $4" = '78 56 34 12' ]; then
        pass restore_marker_format
    else
        fail restore_marker_format
    fi
else
    fail restore_marker_regular_readable
fi

valid_records=0
for record in /data/nvram/APCFG/APRDEB/BT_Addr /data/BT_Addr; do
    if regular "$record"; then
        bytes=$(wc -c < "$record" 2>/dev/null) || bytes=
        if [ "$bytes" -eq 64 ] 2>/dev/null; then
            pass "record_shape_64_bytes $record"
            valid_records=$((valid_records + 1))
        else
            unknown "record_size_invalid $record"
        fi
    else
        unknown "record_not_available $record"
    fi
done
[ "$valid_records" -gt 0 ] || fail no_complete_bt_record

if [ ! -L /dev/nvram ] && [ -c /dev/nvram ]; then
    unknown nvram_character_node_present_backend_unverified
else
    unknown nvram_raw_restore_backend_unavailable
fi
unknown calibration_provenance_and_dynamic_linking_not_verified
printf 'READ_ONLY_CHECK_ONLY pairing_audio_location_not_tested\n'
[ "$failures" -eq 0 ] || exit 1
[ "$unknowns" -eq 0 ] || exit 2
exit 0

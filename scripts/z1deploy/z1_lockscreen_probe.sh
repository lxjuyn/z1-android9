#!/system/bin/sh
#
# Exercise the Android display power path using explicit sleep and wake keys.
# Run as the console shell user; root is not required.
#
# Prints machine-readable gate and brightness samples. Physical panel
# blanking still requires operator observation.
set -u

PATH=/system/bin:/system/xbin:$PATH
ROOT=${Z1_ROOT:-}
LED_DIR="$ROOT/sys/class/leds/lcd-backlight"
BLANK="$LED_DIR/display_blank"
BRIGHTNESS="$LED_DIR/brightness"
BOOT_ID="$ROOT/proc/sys/kernel/random/boot_id"
HWC=/vendor/lib/hw/hwcomposer.z1.so
CYCLES=${1:-3}
WAIT_SECONDS=${Z1_LOCK_TIMEOUT:-12}
EXPECT_HWC=${Z1_EXPECT_HWC:-}

case "$CYCLES" in
    ''|*[!0-9]*|0)
        echo "usage: $0 [positive-cycle-count]" >&2
        exit 2
        ;;
esac
case "$WAIT_SECONDS" in
    ''|*[!0-9]*|0)
        echo "Z1_LOCK_TIMEOUT must be a positive integer" >&2
        exit 2
        ;;
esac

read_value() {
    cat "$1" 2>/dev/null | tr -d '\r\n'
}

wakefulness() {
    dumpsys power 2>/dev/null |
        sed -n 's/^[[:space:]]*mWakefulness=//p' |
        head -n 1
}

wait_for_state() {
    expected_blank=$1
    expected_wake=$2
    waited=0
    while [ "$waited" -lt "$WAIT_SECONDS" ]; do
        [ "$(read_value "$BOOT_ID")" = "$original_boot" ] || return 1
        actual=$(read_value "$BLANK")
        if [ "$actual" = "$expected_blank" ] && [ "$(wakefulness)" = "$expected_wake" ]; then
            return 0
        fi
        sleep 1
        waited=$((waited + 1))
    done
    return 1
}

sample() {
    label=$1
    echo "$label display_blank=$(read_value "$BLANK")" \
        "brightness=$(read_value "$BRIGHTNESS")" \
        "wakefulness=$(wakefulness)"
}

ORIG_TIMEOUT=$(settings get system screen_off_timeout 2>/dev/null)
ORIG_STAY=$(settings get global stay_on_while_plugged_in 2>/dev/null)

restore_settings() {
    if [ -n "$ORIG_TIMEOUT" ] && [ "$ORIG_TIMEOUT" != null ]; then
        settings put system screen_off_timeout "$ORIG_TIMEOUT" >/dev/null 2>&1
    fi
    if [ -n "$ORIG_STAY" ] && [ "$ORIG_STAY" != null ]; then
        settings put global stay_on_while_plugged_in "$ORIG_STAY" >/dev/null 2>&1
    fi
}
trap restore_settings EXIT
trap 'exit 130' HUP INT TERM

if [ ! -r "$BLANK" ]; then
    echo "summary status=FAIL reason=display_blank_unreadable"
    exit 1
fi

original_boot=$(read_value "$BOOT_ID")
if [ -z "$original_boot" ]; then
    echo "summary status=FAIL reason=boot_id_unreadable"
    exit 1
fi

settings put system screen_off_timeout 30000 >/dev/null 2>&1
settings put global stay_on_while_plugged_in 0 >/dev/null 2>&1
echo "Z1_LOCK_PROBE_BEGIN cycles=$CYCLES boot_id=$original_boot"
echo "hwc_sha256=$(sha256sum "$ROOT$HWC" 2>/dev/null | awk '{print $1}')"
echo "physical_blank_confirmation=MANUAL_REQUIRED"

if [ -n "$EXPECT_HWC" ]; then
    actual_hwc=$(sha256sum "$ROOT$HWC" 2>/dev/null | awk '{print $1}')
    if [ "$actual_hwc" != "$EXPECT_HWC" ]; then
        echo "summary status=FAIL reason=hwc_mismatch expected=$EXPECT_HWC actual=${actual_hwc:-UNREADABLE}"
        exit 1
    fi
fi

if ! input keyevent 224 >/dev/null 2>&1 || ! wait_for_state 0 Awake; then
    sample "initial"
    echo "summary status=FAIL reason=initial_wake_timeout"
    exit 1
fi
sample "initial"

pass=0
fail=0
cycle=1
while [ "$cycle" -le "$CYCLES" ]; do
    if input keyevent 223 >/dev/null 2>&1 && wait_for_state 1 Asleep; then
        sample "cycle=$cycle lock"
        pass=$((pass + 1))
    else
        sample "cycle=$cycle lock"
        echo "cycle=$cycle lock status=FAIL reason=lock_state_not_reached"
        fail=$((fail + 1))
        break
    fi

    if input keyevent 224 >/dev/null 2>&1 && wait_for_state 0 Awake; then
        sample "cycle=$cycle wake"
        pass=$((pass + 1))
    else
        sample "cycle=$cycle wake"
        echo "cycle=$cycle wake status=FAIL reason=wake_state_not_reached"
        fail=$((fail + 1))
        break
    fi
    cycle=$((cycle + 1))
done

if [ "$fail" -eq 0 ] && [ "$(read_value "$BOOT_ID")" = "$original_boot" ]; then
    echo "summary pass=$pass fail=0 status=PASS physical_blank=UNVERIFIED"
    exit 0
fi
if [ "$(read_value "$BOOT_ID")" != "$original_boot" ]; then
    echo "summary pass=$pass status=FAIL reason=boot_id_changed physical_blank=UNVERIFIED"
else
    echo "summary pass=$pass fail=$fail status=FAIL physical_blank=UNVERIFIED"
fi
exit 1

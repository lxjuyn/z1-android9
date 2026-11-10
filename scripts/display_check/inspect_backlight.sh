#!/system/bin/sh
# Passive sysfs inspector. Invoke with sh; Android needs no Python.
# No brightness/trigger/property/settings writes, commands, mounts or ioctls.
# Root is mandatory to avoid accidental inspection of the host machine.
# Filesystem fixtures must be trusted and quiescent; path checks are not a
# sandbox against a concurrent attacker replacing symlinks between reads.
# Dependencies: POSIX shell, readlink -f and head -c (toybox/busybox/coreutils).
# Exit: 0 readable valid provider(s), 2 usage, 3 no provider, 4 incomplete data.

LC_ALL=C
export LC_ALL

usage() {
    cat <<'HELP'
Usage: sh inspect_backlight.sh --root FILESYSTEM_ROOT
       sh inspect_backlight.sh --plan

Read-only: enumerates /sys/class/backlight/* and the Z1 LCD candidate
/sys/class/leds/lcd-backlight. It does not infer any indicator LED exists.
--root / must be explicitly supplied for a future device-side inspection.
A fixture root can contain relative sysfs class symlinks into sys/devices.
Absolute symlinks outside the fixture root are rejected, not followed.
Only regular attributes are read; special files and malformed numbers fail.

Output is key=value blocks with provider_begin/provider_end delimiters.
brightness is the current sysfs reading, not a measurement of panel light.
actual_brightness is optional for LED class; absence is reported separately.
All providers are listed; multiple candidates are never silently selected.
An empty class is not proof that the physical display has no backlight.
Reads are sequential, not an atomic snapshot. No permissions are changed.
Exit 0 validates data shape/range only: it does not prove a working display,
a running HAL, an active panel, a successful suspend, or safe wake sources.
HELP
}

plan() {
    cat <<'PLAN'
FUTURE DEVICE TEST PLAN -- not executed by this tool
1. Establish board identity and hashes of the running kernel/DT/HAL before
   testing. Confirm the physical backlight provider; a sysfs name alone is
   insufficient. Capture this inspector's baseline plus visual observation.
2. Save the original user brightness, auto-brightness mode, brightness and
   max_brightness, selected LED trigger, bl_power where present, and display
   power state. Prepare a verified recovery/wake path before any OFF test.
3. Validate clamp/mapping at Android brightness inputs 0, 1, 42, 43, 254, 255
   using the actual HAL and its measured maximum. Require 0->off and define
   the intended minimum nonzero mapping before changing code. A floor(b*6/255)
   implementation maps 1..42 to zero; this is conditional, not a board result.
   Do not write out-of-range values to physical hardware merely to test clamp.
4. After each approved single-variable change, capture current/max/actual,
   selected trigger and visible panel state. Distinguish a black rendered
   frame, a stalled scanout and an extinguished backlight using a known image
   and a human observation; none can be proven by these sysfs values alone.
5. Restore the exact saved state, including any trigger removed by writing 0,
   and verify both readback and visible brightness. HAL/composer/trigger may
   compete; observe ordering and ensure resume does not restore stale levels.
6. Test Android screen OFF/ON first without releasing existing wake locks.
   It is not evidence of system suspend. Leave timeout/global settings intact.
7. Test real suspend/resume only in a separate hardware-approved session with
   a verified wake source/recovery path; retain existing NO-GO restrictions.
   Record time-stamped kernel suspend/resume evidence and state restoration.
PLAN
}

case ${1-} in
    --help|-h) [ "$#" -eq 1 ] || exit 2; usage; exit 0 ;;
    --plan) [ "$#" -eq 1 ] || exit 2; plan; exit 0 ;;
esac
if [ "$#" -ne 2 ] || [ "$1" != --root ] || [ ! -d "$2" ]; then
    usage >&2
    exit 2
fi
# Prefix a relative operand to avoid option interpretation by readlink.
case $2 in /*) requested_root=$2 ;; *) requested_root=./$2 ;; esac
root=$(readlink -f "$requested_root" 2>/dev/null) || exit 2
[ -n "$root" ] && [ -d "$root" ] || exit 2
[ "$root" = / ] && root=

# Resolve class and attribute links without falling outside an offline root.
resolve() {
    resolved=$(readlink -f "$1" 2>/dev/null) || return 1
    [ -n "$resolved" ] || return 1
    case "$resolved" in "$root"/*) return 0 ;; *) return 1 ;; esac
}

# Number results: nonnegative signed-32-bit decimal; bounded before arithmetic.
# Leading zeros are normalized so shell octal parsing never occurs.
number() {
    raw=$1
    case $raw in ''|*[!0-9]*) return 1 ;; esac
    [ "${#raw}" -le 10 ] || return 1
    while [ "${raw#0}" != "$raw" ] && [ "${#raw}" -gt 1 ]; do raw=${raw#0}; done
    [ "$raw" -le 2147483647 ] 2>/dev/null || return 1
    normalized=$raw
}

# Return value/status through variables; never turn a read failure into zero.
read_attribute() {
    attr_status=missing
    attr_value=unknown
    if [ ! -e "$1" ] && [ ! -L "$1" ]; then return; fi
    if ! resolve "$1"; then attr_status=unsafe_path; return; fi
    if [ ! -f "$resolved" ]; then attr_status=not_regular; return; fi
    if [ ! -r "$resolved" ]; then attr_status=unreadable; return; fi
    # Sentinel preserves trailing newlines for the size and syntax checks.
    attr_value=$(head -c 4097 "$resolved" 2>/dev/null; read_rc=$?; printf '.end'; exit "$read_rc")
    if [ "$?" -ne 0 ]; then attr_status=read_error; attr_value=unknown; return; fi
    attr_value=${attr_value%.end}
    if [ "${#attr_value}" -gt 4096 ]; then attr_status=too_long; attr_value=unknown; return; fi
    newline='
'
    attr_value=${attr_value%"$newline"}
    attr_status=ok
}

count=0
issues=0
emit_provider() {
    class=$1
    candidate=$2
    name=${candidate##*/}
    count=$((count + 1))
    printf 'provider_begin=%s\nclass=%s\n' "$count" "$class"
    case $name in ''|*[!A-Za-z0-9_.:-]*)
        printf 'provider_status=invalid_name\nprovider_end=%s\n' "$count"
        issues=$((issues + 1)); return ;;
    esac
    printf 'name=%s\npath=/sys/class/%s/%s\n' "$name" "$class" "$name"
    if ! resolve "$candidate" || [ ! -d "$resolved" ]; then
        printf 'provider_status=unresolved_or_unsafe\nprovider_end=%s\n' "$count"
        issues=$((issues + 1)); return
    fi
    provider=$resolved
    printf 'provider_status=resolved\n'
    read_attribute "$provider/type"
    if [ "$attr_status" = ok ]; then
        case $attr_value in raw|platform|firmware) ;; *) attr_status=unknown_type; attr_value=unknown ;; esac
    fi
    printf 'type_status=%s
type=%s
' "$attr_status" "$attr_value"
    case $attr_status in ok|missing) ;; *) issues=$((issues + 1));; esac
    # Do not print uevents, device serials, arbitrary fixture text or host paths.
    for attr in brightness max_brightness actual_brightness bl_power; do
        read_attribute "$provider/$attr"
        if [ "$attr_status" = ok ]; then
            if number "$attr_value"; then attr_value=$normalized
            else attr_status=invalid_number; attr_value=unknown; fi
        fi
        printf '%s_status=%s\n%s=%s\n' "$attr" "$attr_status" "$attr" "$attr_value"
        case $attr_status in
            ok) ;;
            missing) case $attr in brightness|max_brightness) issues=$((issues + 1));; esac ;;
            *) issues=$((issues + 1)) ;;
        esac
        case $attr in
            brightness) current=$attr_value; current_status=$attr_status ;;
            max_brightness) maximum=$attr_value; max_status=$attr_status ;;
            actual_brightness) actual=$attr_value; actual_status=$attr_status ;;
            bl_power) power=$attr_value; power_status=$attr_status ;;
        esac
    done
    if [ "$current_status" = ok ] && [ "$max_status" = ok ]; then
        printf 'range_min=0\nrange_max=%s\n' "$maximum"
        if [ "$maximum" -eq 0 ] || [ "$current" -gt "$maximum" ]; then
            printf 'range_check=invalid\n'; issues=$((issues + 1))
        else printf 'range_check=valid\n'; fi
    else printf 'range_check=unknown\n'; fi
    if [ "$actual_status" = ok ] && [ "$max_status" = ok ] && [ "$actual" -gt "$maximum" ]; then
        printf 'actual_range_check=invalid\n'; issues=$((issues + 1))
    elif [ "$actual_status" = ok ] && [ "$max_status" = ok ]; then
        printf 'actual_range_check=valid\n'
    else printf 'actual_range_check=unknown\n'; fi
    if [ "$current_status" = ok ] && [ "$actual_status" = ok ]; then
        if [ "$current" = "$actual" ]; then printf 'current_actual_relation=equal\n'
        else printf 'current_actual_relation=different_not_necessarily_fault\n'; fi
    else printf 'current_actual_relation=unknown\n'; fi
    if [ "$current_status" = ok ]; then
        if [ "$current" = 0 ]; then printf 'brightness_observation=zero\n'
        else printf 'brightness_observation=nonzero\n'; fi
    else printf 'brightness_observation=unknown\n'; fi
    if [ "$power_status" = ok ]; then
        case $power in
            0) printf 'power_observation=unblank_requested\n' ;;
            4) printf 'power_observation=powerdown_requested\n' ;;
            1|2|3) printf 'power_observation=other_fb_blank_code\n' ;;
            *) printf 'power_observation=unknown_code\n'; issues=$((issues + 1)) ;;
        esac
    fi
    read_attribute "$provider/trigger"
    if [ "$attr_status" = ok ]; then
        # Only expose the bracketed selection, not the entire raw file.
        case $attr_value in
            *'['*']'*) selected=${attr_value#*\[}; selected=${selected%%\]*}
                case $selected in ''|*[!A-Za-z0-9_.:-]*) selected=unknown; attr_status=invalid_trigger;; esac ;;
            *) selected=unknown; attr_status=invalid_trigger ;;
        esac
    else selected=unknown; fi
    printf 'trigger_status=%s\nselected_trigger=%s\n' "$attr_status" "$selected"
    case $attr_status in ok|missing) ;; *) issues=$((issues + 1));; esac
    printf 'physical_backlight=unverified\npanel_image=unverified\nprovider_end=%s\n' "$count"
}

printf 'schema=display-check-v1\nmode=passive\nsnapshot_atomic=no\n'
for class in backlight leds; do
    class_path="$root/sys/class/$class"
    if [ ! -e "$class_path" ] && [ ! -L "$class_path" ]; then continue; fi
    if ! resolve "$class_path" || [ ! -d "$resolved" ] || [ ! -r "$resolved" ] || [ ! -x "$resolved" ]; then
        printf 'class_%s_status=unresolved_unreadable_or_unsafe\n' "$class"
        issues=$((issues + 1)); continue
    fi
    if [ "$class" = leds ]; then
        candidate="$class_path/lcd-backlight"
        if [ -e "$candidate" ] || [ -L "$candidate" ]; then emit_provider leds "$candidate"; fi
    else
        for candidate in "$class_path"/*; do
            if [ -e "$candidate" ] || [ -L "$candidate" ]; then emit_provider backlight "$candidate"; fi
        done
    fi
done
printf 'provider_count=%s\nissue_count=%s\n' "$count" "$issues"
if [ "$count" -gt 1 ]; then printf 'selection=multiple_candidates_no_auto_selection\n'
elif [ "$count" -eq 1 ]; then printf 'selection=single_candidate_not_physical_proof\n'
else printf 'selection=none_no_hardware_conclusion\n'; fi
[ "$issues" -eq 0 ] || exit 4
[ "$count" -gt 0 ] || exit 3
exit 0

#!/system/bin/sh
# Isolate the first board step that resets during direct speaker playback.
#
# This intentionally avoids register, DAPM and dmesg readers while tinyplay is
# active. Each stage is emitted to /dev/kmsg and appended to a progress file so
# a reset can be classified from the serial capture and the next boot.

set -u

PATH=/system/bin:/system/xbin:/vendor/bin:/sbin:$PATH
export PATH

BASE=${0%/*}
TONE=${1:-$BASE/z1_audio_tone.wav}
STAGE=/data/local/tmp/z1stage
PROGRESS=${Z1_AUDIO_STAIRCASE_PROGRESS:-$STAGE/z1_audio_staircase.progress}
MIXER_LOG=${Z1_AUDIO_STAIRCASE_MIXER_LOG:-$STAGE/z1_audio_staircase.mixer.log}
PLAY_LOG=${Z1_AUDIO_STAIRCASE_PLAY_LOG:-$STAGE/z1_audio_staircase.play.log}
EXPECTED_TONE_BYTES=${Z1_AUDIO_EXPECTED_TONE_BYTES:-384044}
SYNC_MARKERS=${Z1_AUDIO_STAIRCASE_SYNC:-0}
BOOT_ID=$(cat /proc/sys/kernel/random/boot_id 2>/dev/null || true)
TRIGGER_FAIL=0
ROUTE_ACTIVE=0
PLAY_PID=

mark() {
    _uptime=$(cut -d' ' -f1 /proc/uptime 2>/dev/null || true)
    _line="$(date '+%Y-%m-%dT%H:%M:%S%z' 2>/dev/null || echo unknown) uptime=${_uptime:-unknown} boot_id=${BOOT_ID:-unknown} $*"
    printf '%s\n' "$_line" >> "$PROGRESS" 2>/dev/null || true
    printf '<6>[z1audio-stair] %s\n' "$_line" > /dev/kmsg 2>/dev/null || true
    [ "$SYNC_MARKERS" = 1 ] && sync 2>/dev/null || true
}

fail() {
    TRIGGER_FAIL=1
    mark "FAIL $*"
}

mixer_value() {
    # Query a single control. Android 9 awk can abort on the table parser.
    _mix_line=$(tinymix "$1" 2>&1) || return 1
    case "$_mix_line" in
        "$1: "*) _mix_value=${_mix_line#"$1: "} ;;
        *) return 1 ;;
    esac
    case "$_mix_value" in
        *' ('*')') _mix_value=${_mix_value%%' ('*} ;;
    esac
    case "$_mix_value" in
        On) printf '1\n' ;;
        Off) printf '0\n' ;;
        ''|*[!0-9]*) return 1 ;;
        *) printf '%s\n' "$_mix_value" ;;
    esac
}

set_control() {
    _label=$1
    _name=$2
    _value=$3
    mark "STEP_${_label}_BEFORE name=$_name value=$_value"
    if tinymix "$_name" "$_value" >> "$MIXER_LOG" 2>&1; then
        mark "STEP_${_label}_DONE rc=0"
    else
        _rc=$?
        fail "STEP_${_label}_RC=$_rc name=$_name value=$_value"
    fi
}

restore_route() {
    [ "$ROUTE_ACTIVE" -eq 1 ] || return 0
    mark "STEP_RESTORE_BEGIN"
    tinymix "Speaker Volume" "${SPK_VOLUME_BEFORE:-1}" >> "$MIXER_LOG" 2>&1 || true
    tinymix "Speaker Switch" "${SPK_SWITCH_BEFORE:-1}" >> "$MIXER_LOG" 2>&1 || true
    tinymix "Speaker Route Switch" "${SPK_ROUTE_BEFORE:-1}" >> "$MIXER_LOG" 2>&1 || true
    tinymix "Headphone Switch" "${HP_SWITCH_BEFORE:-1}" >> "$MIXER_LOG" 2>&1 || true
    ROUTE_ACTIVE=0
    mark "STEP_RESTORE_DONE"
}

stop_playback() {
    if [ -n "$PLAY_PID" ]; then
        kill "$PLAY_PID" 2>/dev/null || true
        wait "$PLAY_PID" 2>/dev/null || true
    fi
    exit 130
}

mkdir -p "$STAGE" 2>/dev/null || {
    echo "cannot create $STAGE" >&2
    exit 2
}
: > "$PROGRESS" 2>/dev/null || exit 2
: > "$MIXER_LOG" 2>/dev/null || exit 2
: > "$PLAY_LOG" 2>/dev/null || exit 2
trap restore_route 0
trap stop_playback 1 2 15

mark "STEP_00_START uid=$(id -u 2>/dev/null || echo unknown) tone=$TONE"

if [ "$(id -u 2>/dev/null || echo unknown)" != 0 ]; then
    fail "root_required"
    exit 2
fi
[ -r /proc/asound/cards ] && grep -q 'mt6572-mt6323' /proc/asound/cards ||
    fail "sound_card_missing"
[ -r /proc/asound/card0/pcm0p/sub0/status ] ||
    fail "playback_pcm_status_missing"
[ -x /system/bin/tinymix ] || fail "tinymix_missing"
[ -x /system/bin/tinypcminfo ] || fail "tinypcminfo_missing"
[ -x /system/bin/tinyplay ] || fail "tinyplay_missing"
command -v timeout >/dev/null 2>&1 || fail "timeout_missing"
[ -f "$TONE" ] || fail "tone_missing"
if [ -f "$TONE" ]; then
    _tone_bytes=$(wc -c < "$TONE" 2>/dev/null || echo 0)
    [ "$_tone_bytes" -eq "$EXPECTED_TONE_BYTES" ] ||
        fail "tone_size=$_tone_bytes expected=$EXPECTED_TONE_BYTES"
fi

HP_SWITCH_BEFORE=$(mixer_value "Headphone Switch")
SPK_ROUTE_BEFORE=$(mixer_value "Speaker Route Switch")
SPK_SWITCH_BEFORE=$(mixer_value "Speaker Switch")
SPK_VOLUME_BEFORE=$(mixer_value "Speaker Volume")
mark "STEP_01_MIXER_SAVED headphone=${HP_SWITCH_BEFORE:-missing} route=${SPK_ROUTE_BEFORE:-missing} switch=${SPK_SWITCH_BEFORE:-missing} volume=${SPK_VOLUME_BEFORE:-missing}"
for _value in "$HP_SWITCH_BEFORE" "$SPK_ROUTE_BEFORE" "$SPK_SWITCH_BEFORE" "$SPK_VOLUME_BEFORE"; do
    [ -n "$_value" ] || fail "required_mixer_control_missing"
done

if [ "$TRIGGER_FAIL" -eq 0 ]; then
    mark "STEP_02_MIXER_READ_BEGIN"
    tinymix >> "$MIXER_LOG" 2>&1 || fail "tinymix_read_failed"
    mark "STEP_02_MIXER_READ_DONE"
    sleep 1

    ROUTE_ACTIVE=1
    set_control 03_HEADPHONE_OFF "Headphone Switch" 0
    sleep 1
    set_control 04_SPEAKER_ROUTE_ON "Speaker Route Switch" 1
    sleep 1
    set_control 05_SPEAKER_SWITCH_ON "Speaker Switch" 1
    sleep 1
    set_control 06_SPEAKER_VOLUME "Speaker Volume" 2
    sleep 1
fi

if [ "$TRIGGER_FAIL" -eq 0 ]; then
    mark "STEP_07_TINYPCINFO_BEGIN"
    if tinypcminfo -D 0 -d 0 >> "$MIXER_LOG" 2>&1; then
        mark "STEP_07_TINYPCINFO_DONE rc=0"
    else
        _rc=$?
        fail "STEP_07_TINYPCINFO_RC=$_rc"
    fi
    sleep 1
fi

if [ "$TRIGGER_FAIL" -eq 0 ]; then
    mark "STEP_08_TINYPLAY_BEFORE"
    timeout 6 /system/bin/tinyplay "$TONE" -D 0 -d 0 > "$PLAY_LOG" 2>&1 &
    PLAY_PID=$!
    mark "STEP_09_TINYPLAY_STARTED pid=$PLAY_PID"
    sleep 1
    if kill -0 "$PLAY_PID" 2>/dev/null; then
        _state=$(sed -n 's/^state: //p' /proc/asound/card0/pcm0p/sub0/status 2>/dev/null | head -n 1)
        mark "STEP_10_TINYPLAY_PCM state=${_state:-unknown}"
    else
        mark "STEP_10_TINYPLAY_EXITED_EARLY"
    fi
    wait "$PLAY_PID" 2>/dev/null
    _rc=$?
    PLAY_PID=
    mark "STEP_11_TINYPLAY_DONE rc=$_rc"
    [ "$_rc" -eq 0 ] || fail "tinyplay_rc=$_rc"
fi

restore_route
mark "STEP_12_DONE differential_fail=$TRIGGER_FAIL physical_result=not_tested"
cat "$PROGRESS"
[ "$TRIGGER_FAIL" -eq 0 ]

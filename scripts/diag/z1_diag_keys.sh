#!/system/bin/sh
# ---------------------------------------------------------------------------
# z1_diag_keys.sh - guided key-by-key input evidence capture for the Z1
#
# Answers the questions the boot logs cannot: does every key actually emit an
# event, which /dev/input/event* does it come from, and which key code is it?
# (Z1 specific: 复读 / 跟读 and the power key are the ones we still have no
#  captured event for.)
#
# Modes:
#   sh z1_diag_keys.sh list     # list input devices + capabilities (no press)
#   sh z1_diag_keys.sh all      # guided run: prompts each key, 8s capture each
#   sh z1_diag_keys.sh one <name> [secs]
#                               # capture a single custom key, e.g. one repeat
#   sh z1_diag_keys.sh raw [secs]
#                               # free-form capture of everything (default 20s)
#
# You must run this interactively (adb shell / serial console): the script
# waits for you to press a key while it records getevent output.
#
# Optional env/args:  KEYWIN=<seconds per key>   (default 8)
# Writes only /data/local/z1diag/keys_<ts>/
# ---------------------------------------------------------------------------

PATH=/system/bin:/system/xbin:/vendor/bin:/sbin:$PATH
export PATH
umask 022

BASE=/data/local/z1diag
MODE=$1
ARG2=$2
ARG3=$3
TS=$(date +%Y%m%d_%H%M%S 2>/dev/null)
[ -n "$TS" ] || TS=$(cut -d. -f1 /proc/uptime 2>/dev/null)
OUT=$BASE/keys_$TS

KEYWIN=${KEYWIN:-8}
case "$KEYWIN" in
    ''|*[!0-9]*) KEYWIN=8 ;;
esac

have() { command -v "$1" >/dev/null 2>&1; }
say() {
    printf '[z1diag-keys] %s\n' "$*"
    printf '<6>[z1diag-keys] %s\n' "$*" > /dev/kmsg 2>/dev/null || true
}
fact() { printf '%s=%s\n' "$1" "$2" >> "$FACTS" 2>/dev/null || true; }

mkdir -p "$OUT" 2>/dev/null || { echo "cannot create $OUT"; exit 1; }
FACTS=$OUT/facts.txt
: > "$FACTS" 2>/dev/null || true

if ! have getevent; then
    echo "z1_diag_keys: getevent missing (this build has no getevent?)"
    exit 1
fi

# capture <seconds> <outfile> - record raw events for N seconds
# By default every input device is captured (each line names its own
# /dev/input/eventN). Restrict with Z1DIAG_DEVS, e.g.
#   Z1DIAG_DEVS="/dev/input/event2 /dev/input/event3" sh z1_diag_keys.sh all
DEVS=${Z1DIAG_DEVS:-}
capture() {
    _secs=$1
    _f=$2
    : > "$_f" 2>/dev/null || true
    if have timeout; then
        timeout "$_secs" getevent -lt $DEVS > "$_f" 2>&1 || true
    else
        getevent -lt $DEVS > "$_f" 2>&1 &
        _pid=$!
        sleep "$_secs"
        kill "$_pid" 2>/dev/null || true
        wait "$_pid" 2>/dev/null || true
    fi
}

# summarize <file> - which key codes were seen
summ() {
    _f=$1
    {
        printf 'KEY/BTN tokens:\n'
        grep -o -E 'KEY_[A-Z0-9_]+|BTN_[A-Z0-9_]+' "$_f" 2>/dev/null | sort | uniq -c
        printf 'numeric code lines:\n'
        grep -c 'EV_KEY' "$_f" 2>/dev/null
    } >> "$SUMMARY" 2>/dev/null || true
}

# ---------------------------------------------------------------------------
# shared: device inventory every run
# ---------------------------------------------------------------------------
{
    printf '# input devices at %s\n' "$(date 2>/dev/null)"
    cat /proc/bus/input/devices 2>/dev/null
} > "$OUT/00_input_devices.txt" 2>/dev/null || true
getevent -lp > "$OUT/00_getevent_caps.txt" 2>&1 || true
{
    printf '# keylayouts\n'
    ls -l /system/usr/keylayout /vendor/usr/keylayout 2>/dev/null
    for f in /system/usr/keylayout/*.kl /vendor/usr/keylayout/*.kl; do
        [ -f "$f" ] || continue
        printf '\n--- %s\n' "$f"
        cat "$f" 2>/dev/null
    done
} > "$OUT/00_keylayouts.txt" 2>/dev/null || true

SUMMARY=$OUT/SUMMARY.txt
: > "$SUMMARY" 2>/dev/null || true

fact INPUT_DEVICES "$(grep -c '^N: Name=' /proc/bus/input/devices 2>/dev/null)"
fact KPD_PRESENT "$(grep -c 'mtk-kpd' /proc/bus/input/devices 2>/dev/null)"
fact TPD_PRESENT "$(grep -c 'mtk-tpd' /proc/bus/input/devices 2>/dev/null)"
fact CST836_PRESENT "$(grep -c -i 'cst836' /proc/bus/input/devices 2>/dev/null)"

# ---------------------------------------------------------------------------
# mode: list
# ---------------------------------------------------------------------------
if [ "$MODE" = "list" ] || [ -z "$MODE" ]; then
    echo "----------------------------------------------------------------"
    echo "Input devices:"
    grep -E '^N: Name=|^H: Handlers=' /proc/bus/input/devices 2>/dev/null
    echo "----------------------------------------------------------------"
    echo "Event nodes:"
    ls -l /dev/input 2>/dev/null
    echo "----------------------------------------------------------------"
    echo "Saved device inventory to: $OUT"
    cat "$FACTS" 2>/dev/null
    if [ -z "$MODE" ]; then
        echo
        echo "Next: 'sh $0 all' for the guided per-key run,"
        echo "      'sh $0 raw 20' for a free-form 20s capture."
    fi
    exit 0
fi

# ---------------------------------------------------------------------------
# mode: raw
# ---------------------------------------------------------------------------
if [ "$MODE" = "raw" ]; then
    SECS=$ARG2
    case "$SECS" in
        ''|*[!0-9]*) SECS=20 ;;
    esac
    echo "Recording ALL input events for ${SECS}s - press whatever you want now."
    say "raw capture ${SECS}s"
    capture "$SECS" "$OUT/raw_${SECS}s.txt"
    summ "$OUT/raw_${SECS}s.txt"
    echo "----------------------------------------------------------------"
    cat "$SUMMARY" 2>/dev/null
    echo "raw log: $OUT/raw_${SECS}s.txt"
    exit 0
fi

# ---------------------------------------------------------------------------
# mode: one <name> [secs]
# ---------------------------------------------------------------------------
if [ "$MODE" = "one" ]; then
    NAME=$ARG2
    [ -n "$NAME" ] || NAME=custom
    case "$NAME" in
        *[!A-Za-z0-9_-]*) NAME=custom ;;
    esac
    SECS=$ARG3
    case "$SECS" in
        ''|*[!0-9]*) SECS=$KEYWIN ;;
    esac
    echo "----------------------------------------------------------------"
    echo "Press and release [$NAME] now - recording ${SECS}s."
    say "one-shot capture key=$NAME secs=$SECS"
    capture "$SECS" "$OUT/one_${NAME}.txt"
    summ "$OUT/one_${NAME}.txt"
    cat "$SUMMARY" 2>/dev/null
    exit 0
fi

# ---------------------------------------------------------------------------
# mode: all  (guided, interactive)
# ---------------------------------------------------------------------------
if [ "$MODE" != "all" ]; then
    echo "Usage: sh $0 list|all|raw [secs]|one <name> [secs]"
    exit 2
fi

echo "=================================================================="
echo " Z1 guided key capture"
echo "------------------------------------------------------------------"
echo " ${KEYWIN}s per key. Press AND release the key when prompted."
echo " If you miss the window, ignore that file and re-run the mode:"
echo "   sh $0 one <name>"
echo "=================================================================="
echo
echo " First, a 3s baseline capture with NO key touched (proves silence)."
say "guided run start, baseline"
capture 3 "$OUT/key_00_baseline_none.txt"
summ "$OUT/key_00_baseline_none.txt"

NEXT=0
run_key() {
    # run_key <name> <expected-code-hint>
    NEXT=$((NEXT + 1))
    _n=$1
    _hint=$2
    printf '\n>>> [%02d] PRESS **%s** now  (expected: %s)  - %ss window\n' \
        "$NEXT" "$_n" "$_hint" "$KEYWIN"
    say "prompt ${NEXT} key=$_n expected=$_hint"
    _f=$OUT/key_$(printf '%02d' "$NEXT")_$_n.txt
    capture "$KEYWIN" "$_f"
    # immediate feedback so the operator knows it worked
    _seen=$(grep -o -E 'KEY_[A-Z0-9_]+|BTN_[A-Z0-9_]+' "$_f" 2>/dev/null | sort -u | tr '\n' ',' )
    if [ -n "$_seen" ]; then
        printf '    captured: %s\n' "$_seen"
    else
        printf '    captured: NOTHING (no EV_KEY during the window)\n'
    fi
    printf '\n### key=%s expected=%s\n' "$_n" "$_hint" >> "$SUMMARY" 2>/dev/null || true
    summ "$_f"
}

echo
echo "--- physical keypad (mtk-kpd) ---"
run_key power        "KEY_POWER (116) on kpd or pmic-keys"
run_key vol_up       "KEY_VOLUMEUP (115)"
run_key vol_down     "KEY_VOLUMEDOWN (114)"
run_key back         "KEY_BACK (158)"
run_key home         "KEY_HOME (102)"
run_key recent       "KEY_MENU->APP_SWITCH (139/187)"
run_key dpad_up      "KEY_UP (103)"
run_key dpad_down    "KEY_DOWN (108)"
run_key dpad_left    "KEY_LEFT (105)"
run_key dpad_right   "KEY_RIGHT (106)"
run_key center       "KEY_ENTER (28) / KEY_KPENTER"
run_key play_pause   "KEY_PLAYPAUSE (164)"
run_key f1           "KEY_F1 (59) - factory READ_T1 (250)"
run_key f2           "KEY_F2 (60) - factory READ_T2 (251)"

echo
echo "--- Z1 learning keys (the open question) ---"
run_key repeat_fudu  "unknown: press the 复读 key, then read SUMMARY.txt"
run_key follow_gendu "unknown: press the 跟读 key, then read SUMMARY.txt"

echo
echo "--- capacitive bottom keys + touch (mtk-tpd / z1_cst836u) ---"
run_key touch_back   "KEY_BACK (158) from touch panel"
run_key touch_home   "KEY_HOME (102) from touch panel"
run_key touch_recent "APP_SWITCH (187) / KEY_MENU (139)"
run_key touch_tap    "ABS_MT_POSITION_X/Y + BTN_TOUCH while tapping"

echo
echo "=================================================================="
echo " SUMMARY (see $SUMMARY)"
echo "=================================================================="
cat "$SUMMARY" 2>/dev/null

# Which keys produced no key event at all?
MISSING=$OUT/MISSING_KEYS.txt
: > "$MISSING" 2>/dev/null || true
for f in "$OUT"/key_*.txt; do
    [ -f "$f" ] || continue
    n=$(basename "$f")
    c=$(grep -c 'EV_KEY' "$f" 2>/dev/null)
    [ -n "$c" ] || c=0
    if [ "$c" -eq 0 ]; then
        printf '%s -> NO EV_KEY\n' "$n" >> "$MISSING" 2>/dev/null || true
    fi
done

echo
echo "Keys/files with no EV_KEY:"
cat "$MISSING" 2>/dev/null
echo
echo "All files: $OUT"
say "guided run done: $OUT"
cat "$FACTS" 2>/dev/null
exit 0

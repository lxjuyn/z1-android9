#!/system/bin/sh
# ---------------------------------------------------------------------------
# z1_diag_audio.sh - Z1 audio deep-dive evidence probe
#
# Chain under test: MT6572 AFE + MT6323 codec (gabin8 ASoC machine driver)
#                   -> tinyalsa (/dev/snd) -> legacy MT6572 audio HAL
#                   -> AudioFlinger / AudioPolicy.
#
# Usage:
#   sh z1_diag_audio.sh           # passive only (mixer dump + state)
#   sh z1_diag_audio.sh trigger   # also try a real playback + capture
#
# The trigger is best-effort and tries three paths in order, recording which
# one worked:
#   1. tinyplay  + a locally generated 1s noise WAV  (kernel PCM path)
#   2. tinycap   (capture path)
#   3. input keyevent 24            (AudioFlinger path via the volume UI)
# Nothing outside /data/local/z1diag is written.
# ---------------------------------------------------------------------------

PATH=/system/bin:/system/xbin:/vendor/bin:/sbin:$PATH
export PATH
umask 022

BASE=/data/local/z1diag
MODE=$1
TS=$(date +%Y%m%d_%H%M%S 2>/dev/null)
[ -n "$TS" ] || TS=$(cut -d. -f1 /proc/uptime 2>/dev/null)
OUT=$BASE/audio_$TS

have() { command -v "$1" >/dev/null 2>&1; }
say() {
    printf '[z1diag-audio] %s\n' "$*"
    printf '<6>[z1diag-audio] %s\n' "$*" > /dev/kmsg 2>/dev/null || true
}
cap() {
    _o=$1; _l=$2; _c=$3
    { printf '\n=== %s\n' "$_l"; sh -c "$_c" 2>&1; printf '=== end %s\n' "$_l"; } >> "$_o" 2>/dev/null || true
}
hdr() {
    : > "$2" 2>/dev/null || true
    { printf '# %s\n# %s\n' "$1" "$(date 2>/dev/null)"; } >> "$2" 2>/dev/null || true
}
fact() { printf '%s=%s\n' "$1" "$2" >> "$FACTS" 2>/dev/null || true; }

TO=
have timeout && TO="timeout 60"

mkdir -p "$OUT" 2>/dev/null || { echo "cannot create $OUT"; exit 1; }
FACTS=$OUT/facts.txt
: > "$FACTS" 2>/dev/null || true

say "start mode=${MODE:-passive} out=$OUT"

# ---------------------------------------------------------------------------
# 1. passive
# ---------------------------------------------------------------------------
CUR=$OUT/01_state.txt
hdr "Audio state before any trigger" "$CUR"
cap "$CUR" "/proc/asound/cards" "cat /proc/asound/cards"
cap "$CUR" "/proc/asound/devices" "cat /proc/asound/devices"
cap "$CUR" "/proc/asound/pcm" "cat /proc/asound/pcm"
cap "$CUR" "/proc/asound timers" "cat /proc/asound/timers 2>/dev/null | head -n 40"
cap "$CUR" "/dev/snd nodes" "ls -l /dev/snd"
cap "$CUR" "card ids" \
    'for f in /proc/asound/card*/id; do [ -r "$f" ] && printf "%s: %s\n" "$f" "$(cat "$f")"; done'
cap "$CUR" "pcm substream status" \
    'for s in /proc/asound/card*/pcm*/sub*/status; do [ -r "$s" ] && printf "%s: %s\n" "$s" "$(head -n 1 "$s")"; done'
cap "$CUR" "asoc debugfs if mounted" \
    "ls /sys/kernel/debug/asoc 2>/dev/null; cat /sys/kernel/debug/asoc/*/dais 2>/dev/null | head -n 40"
cap "$CUR" "snd/afe/codec modules" "grep -i -E 'snd|afe|mt6323|gabin|audio' /proc/modules"
cap "$CUR" "audio props" "getprop | grep -i -E 'audio|sound|codec|afe|voice'"
cap "$CUR" "audio HAL libraries" \
    "ls -l /vendor/lib/hw/audio*.so /system/lib/hw/audio*.so 2>/dev/null"
cap "$CUR" "audio policy config" \
    "ls -l /vendor/etc/audio_policy_configuration.xml /system/etc/audio_policy_configuration.xml /vendor/etc/audio_effects.conf 2>/dev/null"
cap "$CUR" "mixer tools present" \
    "ls -l /system/bin/tinymix /system/bin/tinyplay /system/bin/tinycap /system/bin/tinypcminfo 2>/dev/null"
cap "$CUR" "tinymix dump" "tinymix 2>&1 | head -n 200"
cap "$CUR" "tinypcminfo if present" "tinypcminfo -D 0 2>&1 | head -n 60"
cap "$CUR" "dmesg audio lines" \
    "dmesg 2>/dev/null | grep -i -E 'snd|asoc|afe|mt6323|mt-soc|gabin|dai|codec' | tail -n 400"
cap "$CUR" "dumpsys media.audio_flinger head" "$TO dumpsys media.audio_flinger | head -n 150"
cap "$CUR" "dumpsys media.audio_policy head" "$TO dumpsys media.audio_policy | head -n 120"
cap "$CUR" "logcat audio lines" \
    "logcat -d 2>/dev/null | grep -i -E 'audio|AudioFlinger|AudioPolicy|ASoC|tinyalsa' | tail -n 400"

CARDS=$(sed -n '/^ *[0-9]/p' /proc/asound/cards 2>/dev/null | wc -l)
fact AUDIO_CARDS "$CARDS"
fact AUDIO_SND_NODES "$(ls /dev/snd 2>/dev/null | wc -l)"
fact AUDIO_HAL_LIBS "$(ls /vendor/lib/hw/audio*.so /system/lib/hw/audio*.so 2>/dev/null | wc -l)"
fact AUDIO_TINYMIX "$(ls /system/bin/tinymix 2>/dev/null | wc -l)"
fact AUDIO_TINYPLAY "$(ls /system/bin/tinyplay 2>/dev/null | wc -l)"

if [ "$MODE" != "trigger" ]; then
    say "passive mode done: $OUT"
    cat "$FACTS" 2>/dev/null
    exit 0
fi

# ---------------------------------------------------------------------------
# 2. trigger - path 1: tinyplay on a locally generated WAV
# ---------------------------------------------------------------------------
CUR=$OUT/02_trigger.txt
hdr "Audio trigger attempts" "$CUR"
WAV=$OUT/tone.wav

# 1.0 s, 44100 Hz, stereo, 16-bit PCM, white noise (44-byte header + 176400 B)
if [ -w "$OUT" ]; then
    : > "$WAV" 2>/dev/null || true
    printf 'RIFF\064\061\053\000WAVEfmt \020\000\000\000\001\000\002\000\104\254\000\000\020\021\053\000\004\000\020\000data\020\021\053\000' >> "$WAV" 2>/dev/null || true
    dd if=/dev/urandom bs=176400 count=1 >> "$WAV" 2>/dev/null || true
fi
cap "$CUR" "generated wav" "ls -l $WAV"

TI=""
have timeout && TI="timeout 20"

if [ -x /system/bin/tinyplay ]; then
    say "PATH1 tinyplay: playing $WAV"
    PLAY=$OUT/03_playback.txt
    : > "$PLAY" 2>/dev/null || true
    /system/bin/tinyplay "$WAV" > "$PLAY" 2>&1 &
    ppid=$!
    i=0
    while [ $i -lt 12 ]; do
        kill -0 "$ppid" 2>/dev/null || break
        for s in /proc/asound/card*/pcm*p/sub0/status; do
            [ -r "$s" ] && printf '%s: %s\n' "$s" "$(head -n 1 "$s" 2>/dev/null)" >> "$PLAY" 2>/dev/null
        done
        sleep 1
        i=$((i + 1))
    done
    wait "$ppid" 2>/dev/null || true
    cap "$CUR" "tinyplay exit + dmesg during playback" \
        "tail -n 20 $PLAY; echo ---; dmesg 2>/dev/null | grep -i -E 'snd|afe|dai|pcm|under|mt6323' | tail -n 80"
    fact TRIGGER_PATH1 tinyplay
else
    say "PATH1 skipped: no /system/bin/tinyplay on this build"
    fact TRIGGER_PATH1 absent
fi

# ---------------------------------------------------------------------------
# 3. trigger - path 2: tinycap
# ---------------------------------------------------------------------------
if [ -x /system/bin/tinycap ]; then
    say "PATH2 tinycap: recording 3s to $OUT/capture.wav"
    CAPF=$OUT/capture.wav
    $TI /system/bin/tinycap "$CAPF" -D 0 -d 0 -c 2 -r 44100 -b 16 -n 3 >> "$CUR" 2>&1 || true
    cap "$CUR" "capture result" "ls -l $CAPF; head -c 16 $CAPF 2>/dev/null | od -A d -t x1"
    fact TRIGGER_PATH2 tinycap
else
    say "PATH2 skipped: no /system/bin/tinycap"
    fact TRIGGER_PATH2 absent
fi

# ---------------------------------------------------------------------------
# 4. trigger - path 3: AudioFlinger path via the volume UI beep
# ---------------------------------------------------------------------------
say "PATH3: input keyevent 24 (volume up) to make AudioFlinger emit a sound"
: > "$OUT/04_audioflinger.txt" 2>/dev/null || true
if [ -x /system/bin/input ]; then
    # No logcat -c: the ring buffer may hold the only evidence of earlier
    # failures. We only take the tail after the key events below.
    /system/bin/input keyevent 24 >> "$OUT/04_audioflinger.txt" 2>&1 || true
    sleep 2
    /system/bin/input keyevent 25 >> "$OUT/04_audioflinger.txt" 2>&1 || true
    sleep 3
    cap "$OUT/04_audioflinger.txt" "AudioFlinger after volume keys" \
        "$TO dumpsys media.audio_flinger | head -n 120"
    cap "$OUT/04_audioflinger.txt" "logcat after volume keys" \
        "logcat -d 2>/dev/null | grep -i -E 'audio|AudioFlinger|AudioPolicy|Track|out_write' | tail -n 200"
    fact TRIGGER_PATH3 input_keyevent
else
    say "PATH3 skipped: no /system/bin/input"
    fact TRIGGER_PATH3 absent
fi

cap "$CUR" "final dmesg audio lines" \
    "dmesg 2>/dev/null | grep -i -E 'snd|afe|pcm|mt6323|dai' | tail -n 200"

say "done: $OUT  (a human at the speaker is still the only real check)"
cat "$FACTS" 2>/dev/null
exit 0

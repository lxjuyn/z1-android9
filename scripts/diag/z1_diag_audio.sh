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
# The trigger records independent direct PCM playback, capture, and Android
# audio service state:
#   1. tinyplay  + a locally generated low-level PCM tone  (kernel PCM path)
#   2. tinycap   (capture path)
#   3. dumpsys media.audio_flinger  (passive; use a real app for playback)
# Nothing outside /data/local/z1diag is written.
# ---------------------------------------------------------------------------

PATH=/system/bin:/system/xbin:/vendor/bin:/sbin:$PATH
export PATH
umask 022

BASE=/data/local/z1diag
MODE=${1:-passive}
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
cap "$CUR" "audio HAL libraries (legacy primary + HIDL impl)" \
    "ls -l /vendor/lib/hw/*audio*.so /system/lib/hw/*audio*.so /vendor/lib/hw/android.hardware.audio* /system/lib/hw/android.hardware.audio* 2>/dev/null"
cap "$CUR" "audio module loader" \
    "ls -l /system/lib/modules/load_audio.sh 2>/dev/null; grep -i -E 'snd|afe|gabin|mt6323' /system/lib/modules/load_audio.sh 2>/dev/null"
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
fact AUDIO_HAL_LIBS "$(ls /vendor/lib/hw/*audio*.so /system/lib/hw/*audio*.so /vendor/lib/hw/android.hardware.audio* 2>/dev/null | wc -l)"
fact AUDIO_LOADER "$(ls /system/lib/modules/load_audio.sh 2>/dev/null | wc -l)"
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

# 3 s, 48 kHz, stereo PCM16, 500 Hz, peak 1024/32768 (-30 dBFS).
# This bypasses AudioFlinger and tests the kernel/codec/speaker path only.
if [ -w "$OUT" ]; then
    {
        printf 'RIFF$\312\010\000WAVEfmt \020\000\000\000\001\000\002\000\200\273\000\000\000\356\002\000\004\000\020\000data\000\312\010\000'
        LC_ALL=C awk 'BEGIN {
            for (i = 0; i < 144000; i++) {
                hi = (i % 96 < 48) ? 4 : 252
                printf "%c%c%c%c", 0, hi, 0, hi
            }
        }'
    } > "$WAV"
fi
wav_bytes=$(wc -c < "$WAV" 2>/dev/null) || wav_bytes=0
[ "$wav_bytes" -eq 576044 ] || say "invalid test PCM size ($wav_bytes); skip playback"

cap "$CUR" "generated wav" "ls -l $WAV"

if [ -x /system/bin/tinyplay ] && [ "$wav_bytes" -eq 576044 ] && have timeout; then
    say "PATH1 tinyplay: playing $WAV"
    PLAY=$OUT/03_playback.txt
    : > "$PLAY" 2>/dev/null || true
    timeout 12 /system/bin/tinyplay "$WAV" -D 0 -d 0 > "$PLAY" 2>&1 &
    ppid=$!
    i=0
    while [ $i -lt 12 ]; do
        kill -0 "$ppid" 2>/dev/null || break
        for s in /proc/asound/card*/pcm*p/sub0/status; do
            [ -r "$s" ] && printf '%s: %s\n' "$s" "$(head -n 1 "$s" 2>/dev/null)" >> "$PLAY" 2>/dev/null
        done
        for w in /sys/kernel/debug/asoc/*/dapm/Speaker; do
            [ -r "$w" ] && head -n 1 "$w" >> "$PLAY"
        done
        if [ -r /sys/kernel/debug/gpio ]; then
            grep -E 'gpio-140([[:space:]]|$)|speaker-enable' /sys/kernel/debug/gpio >> "$PLAY" || true
        fi
        sleep 1
        i=$((i + 1))
    done
    wait "$ppid" 2>/dev/null
    play_rc=$?
    fact TRIGGER_PATH1_RC "$play_rc"
    cap "$CUR" "tinyplay exit + dmesg during playback" \
        "tail -n 20 $PLAY; echo ---; dmesg 2>/dev/null | grep -i -E 'snd|afe|dai|pcm|under|mt6323' | tail -n 80"
    fact TRIGGER_PATH1 tinyplay
else
    say "PATH1 skipped: usable WAV, tinyplay and timeout are all required"
    fact TRIGGER_PATH1 absent
fi

# ---------------------------------------------------------------------------
# 3. trigger - path 2: tinycap
# ---------------------------------------------------------------------------
if [ -x /system/bin/tinycap ] && have timeout; then
    say "PATH2 tinycap: recording 3s from AWB pcmC0D1c"
    CAPF=$OUT/capture.wav
    timeout 12 /system/bin/tinycap "$CAPF" -D 0 -d 1 -c 2 -r 48000 -b 16 -T 3 >> "$CUR" 2>&1
    capture_rc=$?
    fact TRIGGER_PATH2_RC "$capture_rc"
    cap "$CUR" "capture result" "ls -l $CAPF 2>/dev/null; od -A d -t x1 -N 44 $CAPF 2>/dev/null"
    if [ "$capture_rc" -eq 0 ] && [ -r "$CAPF" ]; then
        # This tinycap writes a fixed 44-byte WAV header before the PCM.
        capture_bytes=$(wc -c < "$CAPF")
        if [ "$capture_bytes" -gt 44 ]; then
            nonzero=$(od -An -v -t u1 -j 44 "$CAPF" | awk '{ for (i=1;i<=NF;i++) if ($i != 0) n++ } END { print n+0 }')
            fact CAPTURE_PCM_BYTES "$((capture_bytes - 44))"
            fact CAPTURE_NONZERO_PCM_BYTES "$nonzero"
        else
            say "PATH2 produced no PCM payload"
        fi
    fi
    fact TRIGGER_PATH2 tinycap
else
    say "PATH2 skipped: tinycap and timeout are required"
    fact TRIGGER_PATH2 absent
fi

# ---------------------------------------------------------------------------
# 4. AudioFlinger inspection. Volume key events are not a playback test.
# ---------------------------------------------------------------------------
say "PATH3: use a media app to play the tone; service dumps alone do not prove playback"
fact TRIGGER_PATH3 manual_playback_required

cap "$CUR" "final dmesg audio lines" \
    "dmesg 2>/dev/null | grep -i -E 'snd|afe|pcm|mt6323|dai' | tail -n 200"

say "done: $OUT  (a human at the speaker is still the only real check)"
cat "$FACTS" 2>/dev/null
exit 0

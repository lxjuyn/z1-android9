#!/system/bin/sh
# Configure the finite MT6323/MT6572 DAPM graph used by the Z1 primary HAL.

log() { printf '<6>[z1audio] %s\n' "$*" > /dev/kmsg 2>/dev/null || true; }

i=0
while [ ! -e /dev/snd/controlC0 ] && [ "$i" -lt 60 ]; do
    sleep 1
    i=$((i + 1))
done
[ -e /dev/snd/controlC0 ] || {
    log "no controlC0 after 60s"
    exit 1
}

failures=0
set_one() {
    out=$(tinymix "$1" "$2" 2>&1)
    rc=$?
    log "set [$1]=$2 rc=$rc ${out:+-> $out}"
    [ "$rc" -eq 0 ] || failures=$((failures + 1))
}

# Playback path: DL1 -> DAC -> class-D speaker. The jack event can suppress
# Speaker independently through DAPM when headphones are inserted.
set_one "Speaker Route Switch" 1
set_one "Speaker Switch" 1
set_one "Speaker Volume" 1

# Headphone path remains available for the wired hearing-aid routes.
set_one "Headphone Switch" 1
set_one "Headphone Volume" 18

# Capture path: AWB memory interface -> ADDA uplink -> MT6323 MIC1.
set_one "AWB Source" MIC
set_one "MIC Route Switch" 1
set_one "MIC Input Mux" MIC1

count=$(tinymix 2>/dev/null | wc -l)
log "mixer now lists $count controls, failures=$failures"
tinymix 2>/dev/null | grep -iE 'Speaker|Headphone|AWB|MIC' |
    while IFS= read -r line; do log "  $line"; done

[ "$failures" -eq 0 ]

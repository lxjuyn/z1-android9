#!/system/bin/sh
# One-shot audio state probe for bring-up: logs card/ctl/pcm presence and
# the first controls so the next boot immediately shows what the ASoC stack
# registered. Purely diagnostic.
log() { printf '<6>[z1audio] %s\n' "$*" > /dev/kmsg 2>/dev/null || true; }

log "cards: $(cat /proc/asound/cards 2>/dev/null | tr '\n' ' ')"
for d in /dev/snd/*; do
    [ -e "$d" ] && log "dev: $d"
done
[ -f /proc/asound/card0/id ] && log "card0 id: $(cat /proc/asound/card0/id)"
for p in /proc/asound/card*/pcm*p/sub0/status; do
    [ -r "$p" ] && log "$p: $(head -1 "$p" 2>/dev/null)"
done
log "modules: $(grep -E '^(snd|mt6572|mt6323)' /proc/modules 2>/dev/null | awk '{print $1}' | tr '\n' ' ')"

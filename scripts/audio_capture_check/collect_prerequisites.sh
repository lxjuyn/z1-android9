#!/system/bin/sh
# Passive metadata. No device opens, PCM ioctls, mixer commands or recording.
set -eu
usage() { printf '%s\n' 'Usage: sh collect_prerequisites.sh --live | --root ABSOLUTE_FIXTURE'; }
case "${1-}" in
 --live) [ "$#" -eq 1 ] || exit 2; root=; mode=live_metadata ;;
 --root) [ "$#" -eq 2 ] || exit 2; root=${2%/}; mode=fixture_metadata
  case "$root" in ''|/|/proc|/sys|/dev|*/../*|*/..|*/./*|*/.) exit 2;; /*) ;; *) exit 2;; esac
  [ -d "$root" ] && [ ! -L "$root" ] || exit 2 ;;
 --help) usage; exit 0 ;;
 *) usage >&2; exit 2 ;;
esac
for rel in proc proc/asound dev dev/snd system system/bin vendor vendor/bin; do
 [ ! -L "$root/$rel" ] || { printf '%s\n' 'ERROR=symlink_hierarchy' >&2; exit 2; }
done
printf '%s\n' 'Z1_CAPTURE_SNAPSHOT_V1' "MODE=$mode"
for rel in system/bin/tinycap vendor/bin/tinycap system/bin/tinymix vendor/bin/tinymix; do
 key=$(printf '%s' "$rel" | tr / _)
 if [ ! -L "$root/$rel" ] && [ -f "$root/$rel" ] && [ -x "$root/$rel" ]; then value=present_not_executed; else value=missing_or_not_regular_executable; fi
 printf 'TOOL_%s=%s\n' "$key" "$value"
done
pcm="$root/proc/asound/pcm"
if [ ! -r "$pcm" ] || [ ! -f "$pcm" ] || [ -L "$pcm" ]; then printf '%s\n' 'PROC_PCM=unavailable'; exit 3; fi
printf '%s\n' 'PROC_PCM=readable'
ids=$(LC_ALL=C awk '/AWB/ && /capture [1-9][0-9]*/ && /^[0-9][0-9]-[0-9][0-9]:/ {print substr($0,1,5)}' "$pcm")
if [ -z "$ids" ]; then printf '%s\n' 'AWB=absent'; exit 3; fi
printf '%s\n' 'AWB=present'
for id in $ids; do
 card=${id%-*}; device=${id#*-}
 card=$(expr "$card" + 0) || :; device=$(expr "$device" + 0) || :
 printf 'PCM=%s:%s\n' "$card" "$device"
 node="$root/dev/snd/pcmC${card}D${device}c"
 if [ ! -L "$node" ] && [ -c "$node" ]; then kind=character; else kind=missing_or_not_character; fi
 printf 'NODE_%s_%s=%s\n' "$card" "$device" "$kind"
 if [ ! -L "$node" ] && [ -r "$node" ] && [ -w "$node" ]; then access=rw_by_shell_identity; else access=unavailable_by_shell_identity; fi
 printf 'ACCESS_%s_%s=%s\n' "$card" "$device" "$access"
 dir="$root/proc/asound/card$card/pcm${device}c/sub0"
 for path in "$root/proc/asound/card$card" "$root/proc/asound/card$card/pcm${device}c" "$dir"; do
  [ ! -L "$path" ] || { printf '%s\n' 'ERROR=symlink_pcm_hierarchy' >&2; exit 2; }
 done
 if [ -f "$dir/status" ] && [ -r "$dir/status" ] && [ ! -L "$dir/status" ]; then
  state=$(LC_ALL=C awk '/^closed$/ {print "closed"; exit} /^state:[[:space:]]+[A-Z_]+$/ {print $2; exit}' "$dir/status")
 else state=; fi
 printf 'STATE_%s_%s=%s\n' "$card" "$device" "${state:-unknown}"
done
# Exit zero means metadata collected, never verified capture readiness.
exit 0

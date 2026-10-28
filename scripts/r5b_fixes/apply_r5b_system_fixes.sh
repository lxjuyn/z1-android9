#!/bin/bash
# apply_r5b_system_fixes.sh — apply the three zero-compile R5b system fixes to a
# system.img copy.  Idempotent; refuses to touch the input in place.
#
#   A3  add /usr/keylayout/mt6779-keypad.kl  (kernel registers "mt6779-keypad";
#       the shipped .kl used an underscore so EventHub silently fell back to
#       Generic.kl and every Z1 key mapping was lost)
#   A7  strip the wifi/bluetooth <unavailable-feature> declarations from
#       /etc/permissions/z1-mainline-features.xml  (they contradicted the
#       already-shipped WiFi/BT HIDL stack)
#   A6  load z1_charger.ko from the existing post-fs hook
#       /lib/modules/load_audio.sh  (the .ko and its MFD cell/DT node were
#       already present but nothing ever insmod'd it)
#
# usage: apply_r5b_system_fixes.sh <in.img> <out.img>
set -eu
IN=${1:?in.img}
OUT=${2:?out.img}
D=$(cd "$(dirname "$0")" && pwd)
[ "$IN" != "$OUT" ] || { echo "refusing to modify input in place" >&2; exit 1; }
cp -a "$IN" "$OUT"

setmode() { debugfs -w -R "sif $1 mode $2" "$OUT" >/dev/null 2>&1
            debugfs -w -R "sif $1 uid 0"   "$OUT" >/dev/null 2>&1
            debugfs -w -R "sif $1 gid 0"   "$OUT" >/dev/null 2>&1; }
rewrite() { # path srcfile mode
  debugfs -w -R "rm $1" "$OUT" >/dev/null 2>&1 || true
  debugfs -w -R "write $2 $1" "$OUT" >/dev/null 2>&1
  setmode "$1" "$3"
}

echo "A3: /usr/keylayout/mt6779-keypad.kl"
debugfs -w -R "rm /usr/keylayout/mt6779-keypad.kl" "$OUT" >/dev/null 2>&1 || true
debugfs -w -R "write $D/mt6779-keypad.kl /usr/keylayout/mt6779-keypad.kl" "$OUT" >/dev/null 2>&1
setmode /usr/keylayout/mt6779-keypad.kl 0100644

echo "A7: /etc/permissions/z1-mainline-features.xml"
rewrite /etc/permissions/z1-mainline-features.xml "$D/z1-mainline-features.xml.fixed" 0100644

echo "A6: /lib/modules/load_audio.sh"
rewrite /lib/modules/load_audio.sh "$D/load_audio.sh.with_charger" 0100755

echo "done -> $OUT"
echo "verify: e2fsck -fn $OUT"

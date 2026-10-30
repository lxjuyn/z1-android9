#!/usr/bin/env bash
# revert_hidl_hal.sh — undo apply_hidl_hal.sh on an image, offline.
#
# Removes the deployed service binaries + .rc files and strips the light/vibrator/power/
# vendor.lineage.power <hal> blocks back out of /vendor/etc/vintf/manifest.xml.
# The manifest is edited by removing exactly those <hal> elements and rewriting the rest
# byte-for-byte; it is re-parsed before being written.
#
# USAGE: ./revert_hidl_hal.sh --image <deployed.img> --out <restored.img>
# Never edits in place. Never touches the device.
set -euo pipefail

IMAGE="" OUT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --image) IMAGE="$2"; shift 2;;
    --out)   OUT="$2";   shift 2;;
    -h|--help) sed -n '2,12p' "$0"; exit 0;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done
[ -n "$IMAGE" ] && [ -n "$OUT" ] || { echo "ERROR: --image and --out required" >&2; exit 2; }
[ -f "$IMAGE" ] || { echo "ERROR: no such image: $IMAGE" >&2; exit 2; }
[ "$(readlink -f "$IMAGE")" != "$(readlink -f "$OUT" 2>/dev/null || echo "$OUT")" ] \
  || { echo "ERROR: --out must differ from --image" >&2; exit 2; }
command -v debugfs >/dev/null || { echo "ERROR: debugfs required" >&2; exit 2; }

TS="$(date +%Y%m%d_%H%M%S)"
BK="/home/lxj/claude/6572/bridge_backups"; mkdir -p "$BK"
cp -a "$IMAGE" "$BK/$(basename "$IMAGE").pre_revert_$TS"
echo "backup -> $BK/$(basename "$IMAGE").pre_revert_$TS"
cp -a "$IMAGE" "$OUT"
echo "work   -> $OUT"

FILES="
/vendor/bin/hw/android.hardware.light@2.0-service.samsung
/vendor/etc/init/android.hardware.light@2.0-service.samsung.rc
/vendor/bin/hw/android.hardware.vibrator@1.0-service.jf
/vendor/etc/init/android.hardware.vibrator@1.0-service.jf.rc
/vendor/bin/hw/android.hardware.power@1.1-service.jf
/vendor/etc/init/android.hardware.power@1.1-service.jf.rc
"
for f in $FILES; do
  debugfs -w -R "rm $f" "$OUT" >/dev/null 2>&1 && echo "   removed $f" || echo "   (absent) $f"
done

TMPD="$(mktemp -d)"; trap 'rm -rf "$TMPD"' EXIT
debugfs -R "dump /vendor/etc/vintf/manifest.xml $TMPD/m.xml" "$OUT" >/dev/null 2>&1
python3 - "$TMPD/m.xml" "$TMPD/m.new.xml" <<'PY'
import sys, xml.etree.ElementTree as ET
src, dst = sys.argv[1], sys.argv[2]
text = open(src, encoding='utf-8').read()
TARGETS = {
    ("android.hardware.light",   "2.0"),
    ("android.hardware.vibrator","1.0"),
    ("android.hardware.power",   "1.1"),
    ("vendor.lineage.power",     "1.0"),
}
lines = text.splitlines(keepends=True)
out, buf, dropping = [], [], False
def hal_key(buf):
    name = ver = None
    for l in buf:
        s = l.strip()
        if s.startswith("<name>") and name is None:
            name = s[6:-7]
        elif s.startswith("<version>"):
            ver = s[9:-10]
    return name, ver
for l in lines:
    if l.strip() == "<hal format=\"hidl\">":
        buf = [l]; dropping = False; continue
    if buf:
        buf.append(l)
        if l.strip() == "</hal>":
            if hal_key(buf) in TARGETS:
                dropping = True
            if not dropping:
                out.extend(buf)
            buf = []
        continue
    out.append(l)
res = "".join(out)
ET.fromstring(res)
open(dst, "w", encoding="utf-8").write(res)
removed = text.count("<fqname>") - res.count("<fqname>")
print("   removed %d fqname(s) from manifest" % removed)
PY
debugfs -w -R "rm /vendor/etc/vintf/manifest.xml" "$OUT" >/dev/null 2>&1
debugfs -w -R "write $TMPD/m.new.xml /vendor/etc/vintf/manifest.xml" "$OUT" >/dev/null 2>&1
debugfs -w -R "sif /vendor/etc/vintf/manifest.xml mode 0100644" "$OUT" >/dev/null 2>&1
debugfs -w -R "sif /vendor/etc/vintf/manifest.xml uid 0"        "$OUT" >/dev/null 2>&1
debugfs -w -R "sif /vendor/etc/vintf/manifest.xml gid 0"        "$OUT" >/dev/null 2>&1
debugfs -w -R "ea_set /vendor/etc/vintf/manifest.xml security.selinux u:object_r:vendor_configs_file:s0" "$OUT" >/dev/null 2>&1

e2fsck -fn "$OUT" >/dev/null 2>&1 && echo "e2fsck -fn: clean" || { echo "e2fsck FAILED"; exit 1; }
echo "DONE -> $OUT"

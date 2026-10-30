#!/usr/bin/env bash
# apply_hidl_hal.sh — Z1 (MT6572) Android 9: deploy light / vibrator / power HIDL HAL
#
# WHAT IT DOES (all offline, on a COPY of the image — never in place, never on the device):
#   * deploy the service binary to        /vendor/bin/hw/<name>          mode 0755 0:0
#   * deploy its init .rc to              /vendor/etc/init/<name>.rc     mode 0644 0:0
#   * merge (NOT rewrite) fqname entries into /vendor/etc/vintf/manifest.xml
#   * set SELinux labels: ..._exec on binaries, vendor_init_rc on .rc, vendor_configs_file on manifest
#   * verify: XML well-formed -> fsck -fn -> read back sha256
#
# WHY IT IS SHAPED THIS WAY (evidence: out/HIDL_HAL_DEPLOY.md):
#   * All three HALs are `optional="true"` in EVERY framework compatibility matrix in the image
#     (compatibility_matrix.{1,2,3,legacy}.xml). Declaring them in the device manifest can
#     therefore not cause a VINTF "required HAL missing" failure -> vendor boot stays safe.
#   * Manifest is edited by STRING INSERTION of a self-contained <hal> block before the final
#     </manifest>, never by regenerating the document. Existing bytes are preserved verbatim.
#   * labels follow the in-image precedent: gnss/sensors/audio binaries carry
#     hal_<name>_default_exec, .rc files carry vendor_init_rc, manifest carries vendor_configs_file.
#     SELinux on this image is PERMISSIVE (cmdline androidboot.selinux=permissive), so a wrong
#     label is cosmetic, not fatal — but we follow the precedent anyway.
#
# USAGE
#   ./apply_hidl_hal.sh --image <in.img> --out <out.img> --light --vibrator --power [--all]
#
#   --light      deploy android.hardware.light@2.0-service.samsung      (kernel: nothing needed)
#   --vibrator   deploy android.hardware.vibrator@1.0-service.jf        (kernel: NEEDS timed_output, see report)
#   --power      deploy android.hardware.power@1.1-service.jf
#                (+ vendor.lineage.power@1.0 fqname, which that binary also registers)
#   --all        = --light --vibrator --power
#
#   --backup     also make a timestamped copy of the input under bridge_backups/
#                (off by default: the input is never modified, so it already IS the rollback)
#
#   Recommended first step: --light only. It is the only one that needs zero kernel change.
#
# ROLLBACK: ./revert_hidl_hal.sh --image <deployed.img> --out <restored.img>
#           or simply keep using the pre-deploy image; this script never modifies its input.
#
# SAFETY: refuses to run if the output path == input path. Creates a timestamped backup copy of
#         the input first. Does not flash, does not touch USB, does not mount anything.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MAT="$HERE/material"
LIB_MAT="$HERE/material_lib"          # optional: libs to add under /vendor/lib

IMAGE=""
OUT=""
DO_LIGHT=0 DO_VIB=0 DO_POWER=0 DO_BACKUP=0

while [ $# -gt 0 ]; do
  case "$1" in
    --image)    IMAGE="$2"; shift 2;;
    --out)      OUT="$2"; shift 2;;
    --light)    DO_LIGHT=1; shift;;
    --vibrator) DO_VIB=1; shift;;
    --power)    DO_POWER=1; shift;;
    --all)      DO_LIGHT=1; DO_VIB=1; DO_POWER=1; shift;;
    --backup)   DO_BACKUP=1; shift;;
    -h|--help)  sed -n '2,44p' "$0"; exit 0;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done

[ -n "$IMAGE" ] || { echo "ERROR: --image required" >&2; exit 2; }
[ -n "$OUT" ]   || { echo "ERROR: --out required"   >&2; exit 2; }
[ "$DO_LIGHT$DO_VIB$DO_POWER" != "000" ] || { echo "ERROR: pick at least one of --light --vibrator --power" >&2; exit 2; }
[ -f "$IMAGE" ] || { echo "ERROR: no such image: $IMAGE" >&2; exit 2; }
[ "$(readlink -f "$IMAGE")" != "$(readlink -f "$OUT" 2>/dev/null || echo "$OUT")" ] \
  || { echo "ERROR: --out must differ from --image (this script never edits in place)" >&2; exit 2; }

command -v debugfs >/dev/null || { echo "ERROR: debugfs (e2fsprogs) required" >&2; exit 2; }

TS="$(date +%Y%m%d_%H%M%S)"
BK="/home/lxj/claude/6572/bridge_backups"
mkdir -p "$BK"
# The input image is NEVER opened for writing by this script — it works on a fresh copy.
# So the input IS the rollback artifact; a second full 943 MB copy is usually wasted disk.
# Pass --backup if you want an explicit timestamped copy anyway.
if [ "${DO_BACKUP:-0}" = 1 ]; then
  echo "== explicit backup of input =="
  cp -a "$IMAGE" "$BK/$(basename "$IMAGE").pre_hidl_$TS"
  echo "   -> $BK/$(basename "$IMAGE").pre_hidl_$TS"
fi
echo "== rollback reference (input left untouched) =="
echo "   input : $IMAGE"
echo "   sha256: $(sha256sum "$IMAGE" | cut -d' ' -f1)"

echo "== working copy =="
cp -a "$IMAGE" "$OUT"
echo "   -> $OUT"

# ---------------------------------------------------------------- helpers
# deploy a regular file with exact mode/uid/gid and an SELinux label
deploy_file() {
  local src="$1" dst="$2" mode="$3" label="$4"
  debugfs -w -R "rm $dst" "$OUT" >/dev/null 2>&1 || true
  debugfs -w -R "write $src $dst" "$OUT" >/dev/null 2>&1
  debugfs -w -R "sif $dst mode $mode"      "$OUT" >/dev/null 2>&1
  debugfs -w -R "sif $dst uid 0"           "$OUT" >/dev/null 2>&1
  debugfs -w -R "sif $dst gid 0"           "$OUT" >/dev/null 2>&1
  debugfs -w -R "ea_set $dst security.selinux $label" "$OUT" >/dev/null 2>&1
  printf '   deployed %-64s mode %s  label %s\n' "$dst" "$mode" "$label"
}

readback_sha() { debugfs -R "dump $1 /dev/stdout" "$OUT" 2>/dev/null | sha256sum | cut -d' ' -f1; }

# ---------------------------------------------------------------- 1. binaries + rc
if [ "$DO_LIGHT" = 1 ]; then
  deploy_file "$MAT/vendor/bin/hw/android.hardware.light@2.0-service.samsung" \
              "/vendor/bin/hw/android.hardware.light@2.0-service.samsung" 0100755 \
              "u:object_r:hal_light_default_exec:s0"
  deploy_file "$MAT/vendor/etc/init/android.hardware.light@2.0-service.samsung.rc" \
              "/vendor/etc/init/android.hardware.light@2.0-service.samsung.rc" 0100644 \
              "u:object_r:vendor_init_rc:s0"
fi
if [ "$DO_VIB" = 1 ]; then
  deploy_file "$MAT/vendor/bin/hw/android.hardware.vibrator@1.0-service.jf" \
              "/vendor/bin/hw/android.hardware.vibrator@1.0-service.jf" 0100755 \
              "u:object_r:hal_vibrator_default_exec:s0"
  deploy_file "$MAT/vendor/etc/init/android.hardware.vibrator@1.0-service.jf.rc" \
              "/vendor/etc/init/android.hardware.vibrator@1.0-service.jf.rc" 0100644 \
              "u:object_r:vendor_init_rc:s0"
fi
if [ "$DO_POWER" = 1 ]; then
  deploy_file "$MAT/vendor/bin/hw/android.hardware.power@1.1-service.jf" \
              "/vendor/bin/hw/android.hardware.power@1.1-service.jf" 0100755 \
              "u:object_r:hal_power_default_exec:s0"
  deploy_file "$MAT/vendor/etc/init/android.hardware.power@1.1-service.jf.rc" \
              "/vendor/etc/init/android.hardware.power@1.1-service.jf.rc" 0100644 \
              "u:object_r:vendor_init_rc:s0"
fi

# ---------------------------------------------------------------- 2. manifest merge
echo "== vintf manifest merge =="
TMPD="$(mktemp -d)"
trap 'rm -rf "$TMPD"' EXIT
debugfs -R "dump /vendor/etc/vintf/manifest.xml $TMPD/manifest.orig.xml" "$OUT" >/dev/null 2>&1
ORIG_SHA="$(sha256sum "$TMPD/manifest.orig.xml" | cut -d' ' -f1)"
echo "   /vendor/etc/vintf/manifest.xml  before: $ORIG_SHA  ($(stat -c%s "$TMPD/manifest.orig.xml") bytes)"

FRAG="$TMPD/frag.xml"
: > "$FRAG"
if [ "$DO_LIGHT" = 1 ]; then cat >>"$FRAG" <<'EOF'
    <hal format="hidl">
        <name>android.hardware.light</name>
        <transport>hwbinder</transport>
        <version>2.0</version>
        <interface>
            <name>ILight</name>
            <instance>default</instance>
        </interface>
        <fqname>@2.0::ILight/default</fqname>
    </hal>
EOF
fi
if [ "$DO_VIB" = 1 ]; then cat >>"$FRAG" <<'EOF'
    <hal format="hidl">
        <name>android.hardware.vibrator</name>
        <transport>hwbinder</transport>
        <version>1.0</version>
        <interface>
            <name>IVibrator</name>
            <instance>default</instance>
        </interface>
        <fqname>@1.0::IVibrator/default</fqname>
    </hal>
EOF
fi
if [ "$DO_POWER" = 1 ]; then cat >>"$FRAG" <<'EOF'
    <hal format="hidl">
        <name>android.hardware.power</name>
        <transport>hwbinder</transport>
        <version>1.1</version>
        <interface>
            <name>IPower</name>
            <instance>default</instance>
        </interface>
        <fqname>@1.1::IPower/default</fqname>
    </hal>
    <hal format="hidl">
        <name>vendor.lineage.power</name>
        <transport>hwbinder</transport>
        <version>1.0</version>
        <interface>
            <name>ILineagePower</name>
            <instance>default</instance>
        </interface>
        <fqname>@1.0::ILineagePower/default</fqname>
    </hal>
EOF
fi

python3 - "$TMPD/manifest.orig.xml" "$FRAG" "$TMPD/manifest.new.xml" <<'PY'
import sys, xml.etree.ElementTree as ET
src, frag, dst = sys.argv[1], sys.argv[2], sys.argv[3]
text = open(src, encoding='utf-8').read()
block = open(frag, encoding='utf-8').read()
body = block.strip('\n')
# idempotence: skip any <hal> whose fqname is already present
new_fq = [l.strip() for l in body.splitlines() if '<fqname>' in l]
missing = [f for f in new_fq if f not in text]
if not missing:
    print("   manifest already contains all requested fqnames; left byte-identical")
    open(dst, 'w', encoding='utf-8').write(text)
    sys.exit(0)
# keep only the <hal> blocks that carry a still-missing fqname
kept, cur = [], []
for line in body.splitlines():
    cur.append(line)
    if line.strip() == '</hal>':
        b = '\n'.join(cur)
        if any(f in b for f in missing):
            kept.append(b)
        cur = []
insert = '\n'.join(kept)
# insert immediately before the FINAL </manifest>
idx = text.rstrip().rfind('</manifest>')
assert idx != -1, "no closing </manifest> found"
out = text[:idx] + insert + '\n' + text[idx:]
# hard gate: must still parse, and must still carry every original fqname
ET.fromstring(out)
orig_fq = [l.strip() for l in text.splitlines() if '<fqname>' in l]
for f in orig_fq:
    assert f in out, "regression: lost original fqname " + f
open(dst, 'w', encoding='utf-8').write(out)
print("   inserted %d new fqname(s): %s" % (len(missing), ', '.join(missing)))
PY

python3 -c "import xml.etree.ElementTree as ET,sys; ET.parse(sys.argv[1]); print('   XML well-formed: OK')" \
        "$TMPD/manifest.new.xml"

debugfs -w -R "rm /vendor/etc/vintf/manifest.xml" "$OUT" >/dev/null 2>&1
debugfs -w -R "write $TMPD/manifest.new.xml /vendor/etc/vintf/manifest.xml" "$OUT" >/dev/null 2>&1
debugfs -w -R "sif /vendor/etc/vintf/manifest.xml mode 0100644" "$OUT" >/dev/null 2>&1
debugfs -w -R "sif /vendor/etc/vintf/manifest.xml uid 0"        "$OUT" >/dev/null 2>&1
debugfs -w -R "sif /vendor/etc/vintf/manifest.xml gid 0"        "$OUT" >/dev/null 2>&1
debugfs -w -R "ea_set /vendor/etc/vintf/manifest.xml security.selinux u:object_r:vendor_configs_file:s0" "$OUT" >/dev/null 2>&1
echo "   /vendor/etc/vintf/manifest.xml  after : $(sha256sum "$TMPD/manifest.new.xml" | cut -d' ' -f1)  ($(stat -c%s "$TMPD/manifest.new.xml") bytes)"

# ---------------------------------------------------------------- 3. verify
echo "== verify =="
e2fsck -fn "$OUT" >"$TMPD/fsck.txt" 2>&1 && echo "   e2fsck -fn: clean" || { echo "   e2fsck FAILED:"; tail -20 "$TMPD/fsck.txt"; exit 1; }
for f in $( [ "$DO_LIGHT" = 1 ] && echo "/vendor/bin/hw/android.hardware.light@2.0-service.samsung /vendor/etc/init/android.hardware.light@2.0-service.samsung.rc" ) \
         $( [ "$DO_VIB"   = 1 ] && echo "/vendor/bin/hw/android.hardware.vibrator@1.0-service.jf /vendor/etc/init/android.hardware.vibrator@1.0-service.jf.rc" ) \
         $( [ "$DO_POWER" = 1 ] && echo "/vendor/bin/hw/android.hardware.power@1.1-service.jf /vendor/etc/init/android.hardware.power@1.1-service.jf.rc" ); do
  printf '   %-64s sha=%s\n' "$f" "$(readback_sha "$f")"
done
debugfs -R "ea_get /vendor/etc/vintf/manifest.xml security.selinux" "$OUT" 2>/dev/null \
  | grep -m1 'security.selinux' | sed 's/^/   manifest label: /'

echo
echo "DONE -> $OUT"
echo "Do NOT flash from this script. Deployment/flash is a separate, deliberate step."

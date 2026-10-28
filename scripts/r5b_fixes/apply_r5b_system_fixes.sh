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

# --- A2: media_codecs family into /vendor/etc -------------------------------
# A listening device with no media_codecs.xml cannot decode anything: the board
# log shows "MediaCodecsXmlParser: Cannot find media_codecs.xml", and neither
# /etc nor /vendor/etc carried any media_codecs*.xml while every OMX.google.*
# software decoder it names (libstagefright_soft_{aac,mp3,amr,flac,vorbis,avc}
# dec, g711, opus, raw, ...) was already installed in /lib.
#
# The material comes from the LOS16 jfltexx tree (the base this rootfs is built
# from), with its Qualcomm-only OMX.qcom.* entries stripped: those name 8960
# hardware codecs that do not exist on MT6572 and would only be dead entries.
echo "A2: /vendor/etc/media_codecs*.xml"
for f in media_codecs.xml media_codecs_google_audio.xml \
         media_codecs_google_performance.xml media_codecs_google_telephony.xml \
         media_codecs_google_video_le.xml media_codecs_performance.xml; do
  [ -f "$D/media_codecs/$f" ] || { echo "  missing $D/media_codecs/$f" >&2; exit 1; }
  debugfs -w -R "rm /vendor/etc/$f" "$OUT" >/dev/null 2>&1 || true
  debugfs -w -R "write $D/media_codecs/$f /vendor/etc/$f" "$OUT" >/dev/null 2>&1
  setmode "/vendor/etc/$f" 0100644
done

# --- A4b/A5b: upgrade the three GPS binaries to their patched builds ---------
# The shipped image carried the *pre-patch* versions, which reference
# __xlog_buf_printf as a bare undefined symbol instead of declaring
# libxlog.so in DT_NEEDED.  Under Android 9's strict linker namespaces a
# symbol with no NEEDED provider is far more likely to fail resolution, so
# deploy the patched builds that declare it explicitly.
#
#   mnld              30,196 -> 34,685 B   (also adds NEEDED libxlog.so)
#   libmnlp_mt6572    34,572 -> 39,637 B   (also adds NEEDED libxlog.so)
#   gps.default.so    50,584 -> 65,453 B   (also adds NEEDED libxlog.so)
#
# Paths are chosen to match the existing rc/consumers: the init rc starts
# /vendor/bin/mnld, and mnld hardcodes /system/xbin/libmnlp_mt6572.
# 项目根: 脚本位于 <root>/z1a9_gpt/scripts/r5b_fixes/
GPSD=$(cd "$D/../../.." && pwd)
echo "A4b/A5b: patched GPS binaries"
debugfs -w -R "rm /vendor/bin/mnld" "$OUT" >/dev/null 2>&1 || true
debugfs -w -R "write $GPSD/out/z1_gps_stage/deploy/system/xbin/mnld /vendor/bin/mnld" "$OUT" >/dev/null 2>&1
setmode /vendor/bin/mnld 0100755
debugfs -w -R "rm /xbin/libmnlp_mt6572" "$OUT" >/dev/null 2>&1 || true
debugfs -w -R "write $GPSD/out/z1_gps_stage/deploy/system/xbin/libmnlp_mt6572 /xbin/libmnlp_mt6572" "$OUT" >/dev/null 2>&1
setmode /xbin/libmnlp_mt6572 0100755
debugfs -w -R "rm /vendor/lib/hw/gps.default.so" "$OUT" >/dev/null 2>&1 || true
debugfs -w -R "write $GPSD/out/z1_gps_stage/deploy/vendor/lib/hw/gps.default.so /vendor/lib/hw/gps.default.so" "$OUT" >/dev/null 2>&1
setmode /vendor/lib/hw/gps.default.so 0100644

# --- A2b: media_codecs_performance.xml must not name Qualcomm codecs --------
# The earlier copy came from a Qualcomm vendor tree and still listed 6
# OMX.qcom.* entries, contradicting the A2 intent.  Source file now has them
# stripped; re-deploy so the image matches.
echo "A2b: media_codecs_performance.xml (no OMX.qcom)"
debugfs -w -R "rm /vendor/etc/media_codecs_performance.xml" "$OUT" >/dev/null 2>&1 || true
debugfs -w -R "write $D/media_codecs/media_codecs_performance.xml /vendor/etc/media_codecs_performance.xml" "$OUT" >/dev/null 2>&1
setmode /vendor/etc/media_codecs_performance.xml 0100644

# --- A4: libxlog.so into both namespaces -----------------------------------
# mnld / libmnlp_mt6572 / gps.default.so all reference __xlog_buf_printf, and
# nothing in the image provided it.  Ship the stub under /lib (the system
# namespace, where /xbin/libmnlp lives) and /vendor/lib (the vendor namespace,
# where the gnss service and gps.default.so live).
echo "A4: libxlog.so"
for spec in "/lib/libxlog.so:$GPSD/out/z1_gps_stage/deploy/system/lib/libxlog.so" \
            "/vendor/lib/libxlog.so:$GPSD/out/z1_gps_stage/deploy/vendor/lib/libxlog.so"; do
  dst=${spec%%:*}; src=${spec#*:}
  debugfs -w -R "rm $dst" "$OUT" >/dev/null 2>&1 || true
  debugfs -w -R "write $src $dst" "$OUT" >/dev/null 2>&1
  setmode "$dst" 0100644
done

# --- A5: GPS STP transport module -----------------------------------------
# The driver only register_chrdev_region()s major 191 and never device_create()s,
# so the node must be made by hand -- see z1_gps_prep.sh, which now does the
# insmod and the mknod.
echo "A5: mtk_stp_gps_soc.ko"
debugfs -w -R "write $GPSD/out/z1_gps_stage/deploy/system/lib/modules/z1-radio/mtk_stp_gps_soc.ko /lib/modules/z1-radio/mtk_stp_gps_soc.ko" "$OUT" >/dev/null 2>&1
setmode /lib/modules/z1-radio/mtk_stp_gps_soc.ko 0100644

#!/bin/bash
# Install or roll back the MT6323 codec module through z1rootd.
# This never unloads a live module; replacement takes effect after reboot.
set -u
DRY_RUN=0
if [ "${1:-}" = --dry-run ]; then DRY_RUN=1; shift; fi
HERE=$(cd -P -- "$(dirname -- "$0")" && pwd)
PUSH=$HERE/z1push.sh
STAGE=/data/local/tmp/z1stage
TARGET=/system/lib/modules/snd-soc-mt6323.ko
REMOTE=snd-soc-mt6323.ko.candidate
BACKUP=$STAGE/snd-soc-mt6323.ko.backup
WAIT=${Z1_WAIT:-45}
export Z1DEV=${Z1DEV:-/dev/ttyUSB0} Z1LOG
if [ "$DRY_RUN" -eq 0 ]; then : "${Z1LOG:?set Z1LOG to an append-only capture log}"; fi
die(){ printf 'z1_install_audio: ERROR: %s\n' "$*" >&2; exit 1; }
if [ "$DRY_RUN" -eq 0 ]; then
  [ -x "$PUSH" ] || die "missing $PUSH"
  [ -r "$Z1LOG" ] || die "capture log is not readable"
fi
root_cmd(){ printf '%s\n' "$1" | "$PUSH" --rootcmd - --wait "$WAIT" >/dev/null; }
cmd(){ "$PUSH" --cmd "$1" --wait "$WAIT"; }
status(){
  cmd "printf 'audio_target '; sha256sum $TARGET 2>/dev/null || true;
       printf 'audio_loaded '; grep -E '^snd_soc_mt6323 ' /proc/modules 2>/dev/null || true;
       printf 'audio_sysfs '; test -d /sys/module/snd_soc_mt6323 && echo present || echo absent"
}
install_audio(){
  src=$1; [ -f "$src" ] || die "candidate not found: $src"
  sha=$(sha256sum "$src" | awk '{print $1}')
  case $sha in (????????????????????????????????????????????????????????????????) ;; (*) die "invalid candidate hash";; esac
  printf 'installing snd-soc-mt6323.ko sha256=%s\n' "$sha"
  if [ "$DRY_RUN" -eq 1 ]; then
    printf 'dry-run target=%s atomic=cp,cmp,chmod,chown,mv reboot-required\n' "$TARGET"
    return 0
  fi
  "$PUSH" --mode 0644 "$src" "$REMOTE" >/dev/null || die "upload failed"
  root_cmd "set -e
mkdir -p $STAGE/audio-backup
if [ -f $TARGET ]; then cp -p $TARGET $BACKUP; sha256sum $TARGET > $STAGE/audio-backup/original.sha256; stat -c '%a:%u:%g:%s' $TARGET > $STAGE/audio-backup/original.stat; fi
mount -o remount,rw /system
tmp=$TARGET.z1tmp.\$\$
rm -f \"\$tmp\"
cp $STAGE/$REMOTE \"\$tmp\"
cmp -s $STAGE/$REMOTE \"\$tmp\"
chmod 0644 \"\$tmp\"; chown 0:0 \"\$tmp\"
mv -f \"\$tmp\" $TARGET
cmp -s $STAGE/$REMOTE $TARGET
sha256sum $TARGET
mount -o remount,ro /system
mount | grep ' /system ' | head -1" || die "root install failed"
printf 'installed file; reboot required for module load\n'
}
rollback(){
  root_cmd "set -e
[ -f $BACKUP ]
mount -o remount,rw /system
tmp=$TARGET.z1tmp.\$\$; rm -f \"\$tmp\"; cp $BACKUP \"\$tmp\"; cmp -s $BACKUP \"\$tmp\"; chmod 0644 \"\$tmp\"; chown 0:0 \"\$tmp\"; mv -f \"\$tmp\" $TARGET; cmp -s $BACKUP $TARGET; sha256sum $TARGET; mount -o remount,ro /system" || die "rollback failed"
  printf 'rolled back file; reboot required for module load\n'
}
case "${1:-}" in
  install) [ $# -eq 2 ] || die "usage: $0 install MODULE.ko"; install_audio "$2";;
  rollback) rollback;;
  status) status;;
  *) die "usage: $0 [--dry-run] {install MODULE.ko|rollback|status}";;
esac

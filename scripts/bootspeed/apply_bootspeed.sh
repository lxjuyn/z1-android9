#!/bin/bash
# Z1 boot-speedup patch (offline; does not touch out/).
#   usage: apply_bootspeed.sh <src_system.img> <src_boot.img> <outdir>
# Produces in <outdir>:
#   system_bootspeed.img  : /system/framework/arm now resolves to the shipped boot image set
#   boot_z1_bootspeed.img : zygote gets -Xnorelocate -Xnoimage-dex2oat, pm.dexopt.first-boot=verify,
#                           and a post-fs-data hook that drops stale dalvik-cache boot images
set -euo pipefail
SRC_SYS=${1:?src system.img}; SRC_BOOT=${2:?src boot.img}; OUT=${3:?outdir}
HERE=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$OUT"; OUT=$(cd "$OUT" && pwd)
SYS="$OUT/system_bootspeed.img"; BOOT="$OUT/boot_z1_bootspeed.img"
WORK="$OUT/work"; rm -rf "$WORK"; mkdir -p "$WORK"

echo "########## 1/3 system image: make /system/framework/arm resolve to the boot image set"
cp --sparse=always "$SRC_SYS" "$SYS"; chmod u+w "$SYS"
have=$(debugfs -R "ls /framework" "$SYS" 2>/dev/null | tr -s ' \t' '\n' | grep -xE 'arm|arm\.noimage' | tr '\n' ' ')
echo "  /framework entries before: $have"
case " $have " in
  *" arm "*)
  echo "  arm already present -> skipping symlink" ;;
  *)
  debugfs -w -R "symlink /framework/arm arm.noimage" "$SYS" >/dev/null
  echo "  created symlink /framework/arm -> arm.noimage" ;;
esac
debugfs -R "stat /framework/arm" "$SYS" 2>/dev/null | grep -E "Type:|Fast link|Size:" | sed 's/^/  /' || true

echo "########## 2/3 offline gate: verify the boot image set against libart's expectations"
python3 "$HERE/verify_bootset.py" "$SYS" --dir /framework/arm.noimage

echo "########## 3/3 boot image: flags + prop + cache cleanup hook"
python3 "$HERE/bootimg_tool.py" split "$SRC_BOOT" "$WORK/bootparts"
python3 "$HERE/patch_ramdisk.py" "$WORK/bootparts/rootfs.img" "$WORK/bootparts/rootfs_patched.img" "$WORK/ramdisk"
python3 "$HERE/bootimg_tool.py" build "$SRC_BOOT" "$WORK/bootparts/zImage" "$WORK/bootparts/dtb" \
        "$WORK/bootparts/rootfs_patched.img" "$BOOT"

echo "--- round-trip verification of the produced boot.img ---"
rm -rf "$WORK/rt"; python3 "$HERE/bootimg_tool.py" split "$BOOT" "$WORK/rt" >/dev/null
python3 - "$WORK/rt/rootfs.img" <<'PY'
import sys,gzip,subprocess
blob=open(sys.argv[1],'rb').read(); body=blob[512:]
assert body[:10].hex()=='1f8b0800000000000203', 'bad gzip header'
d=gzip.decompress(body)
names=set(subprocess.run(['cpio','-it','--quiet'],input=d,capture_output=True).stdout.decode().split())
for n in ('init.zygote32.rc','default.prop','init.z1.rc','z1_bootimg_clean.sh'):
    assert ('./'+n in names) or (n in names), 'missing '+n
# content assertions
import io,os,tempfile,shutil
t=tempfile.mkdtemp(); subprocess.run(['cpio','-idm','--quiet'],cwd=t,input=d,capture_output=True)
z=open(os.path.join(t,'init.zygote32.rc')).read()
assert '-Xnorelocate' in z and '-Xnoimage-dex2oat' in z, 'zygote flags missing'
p=open(os.path.join(t,'default.prop')).read()
assert 'pm.dexopt.first-boot=verify' in p and 'pm.dexopt.first-boot=quicken' not in p, 'prop not patched'
i=open(os.path.join(t,'init.z1.rc')).read()
assert 'z1_bootimg_clean.sh' in i, 'rc hook missing'
shutil.rmtree(t)
print('  ramdisk content assertions: OK (flags, prop, hook present)')
PY
sz=$(stat -c %s "$BOOT"); [ "$sz" -le 6291456 ] || { echo "ERROR: boot.img $sz > 6MiB"; exit 1; }
echo "  size: $sz bytes (<= 6291456)  magic: $(head -c8 "$BOOT")"

echo
echo "==================== RESULT ===================="
sha256sum "$SYS" "$BOOT"
cat <<TXT

FLASH (device in preloader/BROM, mtkclient waiting):
  cd /home/lxj/claude/6572
  tools/flash_with_retry.sh bootimg   $BOOT /home/lxj/claude/6572/1/boot.img 8
  tools/flash_with_retry.sh system    $SYS  /home/lxj/claude/6572/1/system.img 8
  # (use the exact system partition name/backup pair used by the current flash script)

ONE-TIME, after flashing, from the serial shell (root):
  rm -f /data/dalvik-cache/*/system@framework@boot* /data/dalvik-cache/*/boot*
  # the new ramdisk also does this on every post-fs-data (cheap, idempotent)

ROLLBACK: 原厂 out/1/boot.img + out/1/system.img；或上一版 fix3p boot + v17 system.
TXT

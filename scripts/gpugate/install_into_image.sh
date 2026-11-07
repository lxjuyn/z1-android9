#!/bin/bash
# install_into_image.sh <src system.img> <dst system.img>
# Adds the GPU gate to a Z1 Android 9 system image (offline, debugfs-based).
#
# IMPORTANT: the system partition IS /system, so paths *inside* the image carry
# no "/system" prefix: runtime /system/bin/x == image /bin/x.
set -euo pipefail
SRC=${1:?src system.img}; DST=${2:?dst system.img}
cd /home/lxj/claude/6572

tools/z1_make_system_image.sh "$SRC" "$DST" \
    /bin/z1_gpu_gate.sh:/tmp/gpugate/z1_gpu_gate.sh:0100644 \
    /bin/z1_gpu_watch.sh:/tmp/gpugate/z1_gpu_watch.sh:0100644 \
    /etc/init/z1_gpu_gate.rc:/tmp/gpugate/z1_gpu_gate.rc:0100644

for p in /bin/z1_gpu_gate.sh /bin/z1_gpu_watch.sh /etc/init/z1_gpu_gate.rc; do
    debugfs -w -R "ea_set $p security.selinux u:object_r:system_file:s0" "$DST" >/dev/null 2>&1
done

echo "--- labels ---"
for p in /bin/z1_gpu_gate.sh /bin/z1_gpu_watch.sh /etc/init/z1_gpu_gate.rc; do
    printf '%-32s %s\n' "$p" "$(debugfs -R "ea_list $p" "$DST" 2>/dev/null | grep -o 'u:object_r:[a-z_]*:s0')"
done
echo "--- fsck ---"
e2fsck -fn "$DST" >/tmp/gpugate/e2fsck.out 2>&1 && echo "e2fsck clean" || { echo "e2fsck FAILED"; tail -20 /tmp/gpugate/e2fsck.out; }
echo "dst sha256 = $(sha256sum "$DST" | cut -d' ' -f1)"

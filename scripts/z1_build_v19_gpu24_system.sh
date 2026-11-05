#!/bin/bash
# z1_build_v19_gpu24_system.sh — v19 = v17 + Mesa 24.0.9（替换掉会出彩虹条的 19.1.8）
#
# 与 v18 的区别：v18 用的是项目里流传的 Mesa 19.1.8（板上明确报
# gp error irq state=4000 status=3 = PLB_INVALID_CMD，屏幕彩虹条）；
# v19 用的是仓库 mesa_src(24.0.9) 重编的版本，它的 lima_drm.h 与内核 UAPI 逐字节一致。
#
# 三个文件缺一不可：
#   libGLES_mesa.so  一次装上 44 个 egl* 入口（EGL 装载器按 libGLES_* 通配选它）
#   gallium_dri.so   内含 lima + swrast + kms_swrast
#   libglapi.so      必须同步换：与 libGLES_mesa.so 共享 _glapi_Dispatch/Context
# 另有两个在 19.1.8 上不存在、不加就 dlopen 失败的点（已由构建侧解决）：
#   - 设备没有 atrace_get_enabled_tags（RTLD_NOW 下必炸）→ 链了个 3 行桩
#   - 设备 libdrm 太老（缺 drmDevicesEqual / drmSyncobj*）→ 静态链了 libdrm 2.4.120
set -euo pipefail
cd /home/lxj/claude/6572
SRC=out/z1_v17_system/system_v17.img
STAGE=mesa_android/z1_stage_24
DST=out/z1_v19_gpu24_system/system_v19.img
[ -f "$STAGE/libGLES_mesa.so" ] || { echo "ERR: 先由 mesa24-build 产出 $STAGE"; exit 1; }

mkdir -p "$(dirname "$DST")"
tools/z1_make_system_image.sh "$SRC" "$DST" \
    /vendor/lib/egl/libGLES_mesa.so:"$STAGE/libGLES_mesa.so":0100644 \
    /vendor/lib/dri/gallium_dri.so:"$STAGE/gallium_dri.so":0100644 \
    /vendor/lib/libglapi.so:"$STAGE/libglapi.so":0100644

debugfs -w -R "ea_set /vendor/lib/egl/libGLES_mesa.so security.selinux u:object_r:same_process_hal_file:s0" "$DST" >/dev/null 2>&1
debugfs -w -R "ea_set /vendor/lib/dri/gallium_dri.so security.selinux u:object_r:vendor_file:s0" "$DST" >/dev/null 2>&1
debugfs -w -R "ea_set /vendor/lib/libglapi.so security.selinux u:object_r:vendor_file:s0" "$DST" >/dev/null 2>&1

echo "--- 判定 ---"
debugfs -R "ls /vendor/lib/egl" "$DST" 2>/dev/null | tr ' ' '\n' | grep -E "libGLES|libEGL"
for f in /vendor/lib/egl/libGLES_mesa.so /vendor/lib/dri/gallium_dri.so /vendor/lib/libglapi.so; do
    printf '%-34s %s\n' "$f" "$(debugfs -R "ea_list $f" "$DST" 2>/dev/null | grep -o 'u:object_r:[a-z_]*:s0')"
done
echo "整镜像 sha256(不用于判定) = $(sha256sum "$DST" | cut -d' ' -f1)"
echo "出货件 sha256 = 5de0d4cba83727a15cdb7df42a6ca46c3bb2c447a35e2ca1dc4f46e46e2cdb50"

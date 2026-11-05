#!/bin/bash
# z1_build_v18_gpu_system.sh — v18 = v17 + Mesa(GLES) 软件栈, 用于验证真 GPU 加速
#
# 为什么是"加两个文件"就够了:
#   A9 的 EGL loader(Loader.cpp:320-461)在 load_system_driver() 里按前缀
#   glob /vendor/lib/egl/libGLES_*。只要 libGLES_mesa.so 存在就选它;
#   ro.hardware.egl 在本镜像上无效(没有 sphal 命名空间, load_updated_driver 永不执行),
#   见 out/GPU_PATH_VERIFIED_20261105.md §5。
#
# 判定用 content hash(整镜像 sha256 每次构建都会变, 不要拿它当判据):
#   /vendor/lib/egl/libGLES_mesa.so  aab57fd4a5bc0f02c44f7ecdfe654fc324bbf910a3cf3528ecd516d34f353784
#   /vendor/lib/dri/gallium_dri.so   f7497e4a2090b1736b9358b68645b539adb546c34e754a05310f0e62dffb6032
# 出货件整镜像 sha256 = 6be0c5d58cf0f449faec2a0af01a7c1fa9d819419b6c933ba60719ef9105b038
set -euo pipefail
cd /home/lxj/claude/6572

SRC=out/z1_v17_system/system_v17.img
GPU=out/z1_v14_gpu_system/system_v14.img   # 这两个文件唯一可靠的来源
DST=out/z1_v18_gpu_system/system_v18.img
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT

debugfs -R "dump /vendor/lib/egl/libGLES_mesa.so $TMP/libGLES_mesa.so" "$GPU" >/dev/null 2>&1
debugfs -R "dump /vendor/lib/dri/gallium_dri.so  $TMP/gallium_dri.so"  "$GPU" >/dev/null 2>&1
[ -s "$TMP/libGLES_mesa.so" ] && [ -s "$TMP/gallium_dri.so" ] || { echo "ERR: 从 $GPU 取 Mesa 失败"; exit 1; }

mkdir -p "$(dirname "$DST")"
tools/z1_make_system_image.sh "$SRC" "$DST" \
    /vendor/lib/egl/libGLES_mesa.so:"$TMP/libGLES_mesa.so":0100644 \
    /vendor/lib/dri/gallium_dri.so:"$TMP/gallium_dri.so":0100644

debugfs -w -R "ea_set /vendor/lib/egl/libGLES_mesa.so security.selinux u:object_r:same_process_hal_file:s0" "$DST" >/dev/null 2>&1
debugfs -w -R "ea_set /vendor/lib/dri/gallium_dri.so security.selinux u:object_r:vendor_file:s0" "$DST" >/dev/null 2>&1

echo "--- 判定 ---"
debugfs -R "ls /vendor/lib/egl" "$DST" 2>/dev/null | tr ' ' '\n' | grep -E "libGLES|libEGL"
for f in /vendor/lib/egl/libGLES_mesa.so /vendor/lib/dri/gallium_dri.so; do
    printf '%-34s %s\n' "$f" "$(debugfs -R "ea_list $f" "$DST" 2>/dev/null | grep -o 'u:object_r:[a-z_]*:s0')"
done
echo "整镜像 sha256(不用于判定) = $(sha256sum "$DST" | cut -d' ' -f1)"

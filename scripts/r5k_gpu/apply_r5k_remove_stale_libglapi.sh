#!/bin/bash
# r5k 修复: 删除 Mesa 24.0.9 遗留的 /lib/libglapi.so(抢在 /vendor/lib/libglapi.so 之前被解析,
# 把 GLES dispatch 表尺寸从 1677 撑成 1927 -> mesa 初始化崩 -> 黑屏)。
#
# 判据/机理见 out/EGL_BLACK_SCREEN_FIX.md (归档副本 z1a9_gpt/docs/EGL_BLACK_SCREEN_FIX.md)。
#
# 用法: apply_r5k_remove_stale_libglapi.sh <源img> <目标img> [选项]
#   例: apply_r5k_remove_stale_libglapi.sh \
#         out/z1_r5_system/system.img.r5i_final \
#         out/z1_r5_system/system.img.r5k_final
# 幂等: 已删则跳过。
#   -r  反向恢复(从镜像内备份或 24.0.9 归档写回 /lib/libglapi.so)
#   -b  额外在镜像内留一份备份 /.r5f_gpu_backup/lib_libglapi.so
#       (默认不留: 保持最小改动面 = 与已交付 r5k_final 逐字节一致;
#        回滚请直接重刷主机上的 system.img.r5i_final)
set -euo pipefail

SRC=${1:?需要 <源img>}
DST=${2:?需要 <目标img>}
shift 2 || true

UNDO=""; IN_IMAGE_BACKUP=""
while [ $# -gt 0 ]; do
  case "$1" in
    -r) UNDO="-r" ;;
    -b) IN_IMAGE_BACKUP="-b" ;;
    *) echo "未知选项: $1"; exit 2 ;;
  esac
  shift
done

STALE_PATH=/lib/libglapi.so
# Mesa 24.0.9 产物 sha256 (2026-10-27 交叉编译, 归档 products_mesa_lima_android_20261027_121042)
STALE_SHA=f8420adaf32306b4955124c66a880cba8b4b8faa366faf26216923341f2518aa
VENDOR_GOOD_SHA=9c1ace8554dedf0936dcf32716733f2f2fb122324b08eba1b264c3a94deb1107

echo "== 0. 磁盘余量 =="
df -h . | tail -1
# 本项目反复因盘满导致 debugfs 静默失败 —— 先确认至少有 2G
avail=$(df -Pk . | awk 'NR==2{print $4}')
if [ "$avail" -lt 2097152 ]; then echo "ERROR: 可用 <2G ($avail KB), 拒绝执行"; exit 1; fi

echo "== 1. 复制源 -> 目标 (若不同路径) =="
if [ "$(readlink -f "$SRC")" != "$(readlink -f "$DST")" ]; then
  cp --reflink=auto "$SRC" "$DST"
fi
echo "   源  : $(sha256sum "$SRC" | cut -d' ' -f1)"
echo "   目标: $(sha256sum "$DST" | cut -d' ' -f1)"
[ "$(sha256sum "$SRC" | cut -d' ' -f1)" = "$(sha256sum "$DST" | cut -d' ' -f1)" ] \
  || { echo "ERROR: 复制后 sha 不一致"; exit 1; }

echo "== 2. 检查 /vendor/lib/libglapi.so (必须保持 19.1.8 原样) =="
vh=$(debugfs -R "cat /vendor/lib/libglapi.so" "$DST" 2>/dev/null | sha256sum | cut -d' ' -f1)
[ "$vh" = "$VENDOR_GOOD_SHA" ] || { echo "ERROR: /vendor/lib/libglapi.so sha=$vh != 期望 $VENDOR_GOOD_SHA"; exit 1; }
echo "   OK 19.1.8 未动"

echo "== 3. 处理 $STALE_PATH =="
cur_h=$(debugfs -R "cat $STALE_PATH" "$DST" 2>/dev/null | sha256sum | cut -d' ' -f1)

if [ "$UNDO" = "-r" ]; then
  # 反向: 从镜像内备份恢复(如无备份则从 24.0.9 归档取)
  if [ "$cur_h" != "$(printf '' | sha256sum | cut -d' ' -f1)" ]; then
    echo "   已存在, 无需恢复 (sha=$cur_h)"; exit 0
  fi
  bak=/.r5f_gpu_backup/lib_libglapi.so
  if debugfs -R "stat $bak" "$DST" >/dev/null 2>&1; then
    echo "   从镜像内备份恢复 $bak"
    debugfs -w -R "write $bak $STALE_PATH" "$DST" >/dev/null
  else
    arch=/home/lxj/claude/6572/bridge_backups/products/products_mesa_lima_android_20261027_121042/libglapi.so
    [ -f "$arch" ] || { echo "ERROR: 找不到恢复源 $arch"; exit 1; }
    echo "   从归档恢复 $arch"
    debugfs -w -R "write $arch $STALE_PATH" "$DST" >/dev/null
  fi
  echo "   恢复后 sha=$(debugfs -R "cat $STALE_PATH" "$DST" 2>/dev/null | sha256sum | cut -d' ' -f1)"
else
  if [ "$cur_h" = "$(printf '' | sha256sum | cut -d' ' -f1)" ]; then
    echo "   不存在, 已幂等完成"; exit 0
  fi
  if [ "$cur_h" != "$STALE_SHA" ]; then
    echo "   警告: 现存 sha=$cur_h 与已知 Mesa24.0.9 产物 $STALE_SHA 不同!"
    echo "   仍继续删除(该路径在可用布局 p12 中本就不存在)。"
  fi
  if [ -n "$IN_IMAGE_BACKUP" ]; then
    echo "   备份到 /.r5f_gpu_backup/lib_libglapi.so"
    debugfs -w -R "mkdir /.r5f_gpu_backup" "$DST" >/dev/null 2>&1 || true
    debugfs -R "dump $STALE_PATH /tmp/_r5k_libglapi.bak" "$DST" >/dev/null 2>&1
    debugfs -w -R "write /tmp/_r5k_libglapi.bak /.r5f_gpu_backup/lib_libglapi.so" "$DST" >/dev/null 2>&1 || true
    rm -f /tmp/_r5k_libglapi.bak
  fi
  echo "   删除 $STALE_PATH"
  debugfs -w -R "rm $STALE_PATH" "$DST" >/dev/null
fi

echo "== 4. 回读验证 =="
after=$(debugfs -R "cat $STALE_PATH" "$DST" 2>/dev/null | sha256sum | cut -d' ' -f1)
if [ "$UNDO" = "-r" ]; then
  echo "   $STALE_PATH sha=$after (应为 $STALE_SHA)"
else
  [ "$after" = "$(printf '' | sha256sum | cut -d' ' -f1)" ] || { echo "ERROR: 仍能回读内容"; exit 1; }
  echo "   $STALE_PATH 已消失 OK"
  debugfs -R "ls -l /lib" "$DST" 2>/dev/null | grep -q 'libglapi' && { echo "ERROR: 仍在 /lib 列表"; exit 1; }
  echo "   /lib 列表无 libglapi OK"
fi
for p in /lib/libEGL.so /vendor/lib/egl/libGLES_mesa.so /vendor/lib/dri/gallium_dri.so; do
  printf '   %-38s %s\n' "$p" "$(debugfs -R "cat $p" "$DST" 2>/dev/null | sha256sum | cut -c1-16)"
done

echo "== 5. e2fsck -fn =="
e2fsck -fn "$DST" 2>&1 | tail -3

echo "== 完成 =="
echo "   sha256: $(sha256sum "$DST" | cut -d' ' -f1)"

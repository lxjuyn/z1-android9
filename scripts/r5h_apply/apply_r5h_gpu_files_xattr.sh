#!/bin/bash
# apply_r5h_gpu_files_xattr.sh — 给 apply_gpu_mesa.sh 恢复产生的文件补
#                               security.selinux xattr（幂等）
#
# 背景: apply_gpu_mesa.sh 用 debugfs rm/write/symlink 落盘 —— debugfs 生成全新
# inode 且不写 xattr（同 apply_r5h_xattr.sh 取证的机理）。铁律 6: 新文件进镜像
# 必须补 security.selinux（对齐同目录邻文件真实标签）。
#   /vendor/lib/egl/libGLES_mesa.so        = same_process_hal_file
#       （邻 libEGL_swiftshader.so 实测 + vendor_file_contexts L24 egl(/.*)?，
#         与 apply_r5h_xattr.sh DATA 对 libEGL_mesa.so 的判定同语义）
#   /vendor/lib/egl/libGLESv2_mesa.so      = same_process_hal_file（symlink, -h 打链接本身）
#   /vendor/lib/dri/gallium_dri.so         = vendor_file
#       （目录锚 + plat_file_contexts L6 兜底; apply_r5h_xattr.sh 对 lima_dri.so 同标签）
#   /vendor/lib/dri/{lima,swrast,kms_swrast}_dri.so = vendor_file（symlink, -h）
#       （enforcing 下 lnk_file 无标签 = unlabeled 会被拒，故链接本身也要打）
#
# 与 apply_r5h_xattr.sh 的关系: 该脚本 DATA 清单在 r5f_final 上取证，不含本批
# mesa 新增件; 本脚本补齐缺口。两者可任意顺序（本脚本对 lima_dri.so 用 -h 打
# 链接本身，其目标 gallium_dri.so 由本脚本直接覆盖打标签，先跑谁结果一致）。
#
# 用法:   sudo bash apply_r5h_gpu_files_xattr.sh <system.img>
# 纪律:   拒绝四件只读基线; 已正确标签跳过; 写完 getfattr 回读断言; ro 复读。
set -euo pipefail

BASELINE=(
  /home/lxj/claude/6572/out/z1_r5_system/system.img.r5f_final
  /home/lxj/claude/6572/out/z1_r5_system/system.img.r5_final
  /home/lxj/claude/6572/out/z1_r5c_boot/boot.img
  /home/lxj/claude/6572/out/z1_r41_final/boot.img
)
# path<TAB>label<TAB>symlink?(1=用 -h)
DATA='/vendor/lib/egl/libGLES_mesa.so	u:object_r:same_process_hal_file:s0	0
/vendor/lib/egl/libGLESv2_mesa.so	u:object_r:same_process_hal_file:s0	1
/vendor/lib/dri/gallium_dri.so	u:object_r:vendor_file:s0	0
/vendor/lib/dri/lima_dri.so	u:object_r:vendor_file:s0	1
/vendor/lib/dri/swrast_dri.so	u:object_r:vendor_file:s0	1
/vendor/lib/dri/kms_swrast_dri.so	u:object_r:vendor_file:s0	1'

[ $# -eq 1 ] || { echo "用法: sudo bash $0 <system.img>" >&2; exit 2; }
[ "$(id -u)" -eq 0 ] || { echo "需要 root (mount/setfattr)" >&2; exit 2; }
IMG=$(realpath "$1"); [ -f "$IMG" ] || { echo "镜像不存在: $IMG" >&2; exit 2; }
for b in "${BASELINE[@]}"; do
  [ "$IMG" != "$(realpath -m "$b")" ] || { echo "拒绝: $IMG 是只读基线" >&2; exit 1; }
done
AV=$(df -k / | awk 'NR==2{print $4}'); [ "$AV" -ge 1228800 ] || { echo "磁盘不足 ${AV}K < 1.2G" >&2; exit 1; }

label_of() { getfattr -h --only-values -n security.selinux "$1" 2>/dev/null | tr -d '\0' || true; }

MP=$(mktemp -d /tmp/r5h_gx.XXXXXX)
cleanup() { mountpoint -q "$MP" && umount "$MP" || true; rmdir "$MP" 2>/dev/null || true; }
trap cleanup EXIT
mount -o loop,rw "$IMG" "$MP"

n_patch=0; n_skip=0; n_missing=0
while IFS=$'\t' read -r path want islink; do
  f="$MP$path"
  if [ ! -e "$f" ]; then echo "  路径不存在(跳过): $path"; n_missing=$((n_missing+1)); continue; fi
  cur=$(label_of "$f")
  if [ "$cur" = "$want" ]; then n_skip=$((n_skip+1)); continue; fi
  if [ -n "$cur" ]; then
    echo "  冲突未覆盖: $path 现有=$cur 目标=$want"; continue
  fi
  if [ "$islink" = "1" ]; then setfattr -h -n security.selinux -v "$want" "$f"; \
  else setfattr -n security.selinux -v "$want" "$f"; fi
  got=$(label_of "$f")
  [ "$got" = "$want" ] || { echo "FATAL: $path 回读 '$got' != '$want'" >&2; umount "$MP"; exit 1; }
  echo "  置 $path = $want"
  n_patch=$((n_patch+1))
done < <(printf '%s\n' "$DATA")
sync; umount "$MP"

echo "--- ro 复挂全量回读 ---"
mount -o loop,ro "$IMG" "$MP"
n_bad=0
while IFS=$'\t' read -r path want islink; do
  f="$MP$path"; [ -e "$f" ] || continue
  cur=$(label_of "$f")
  [ "$cur" = "$want" ] || { echo "  回读失败: $path '$cur' != '$want'" >&2; n_bad=$((n_bad+1)); }
done < <(printf '%s\n' "$DATA")
umount "$MP"
[ "$n_bad" -eq 0 ] || { echo "FATAL: $n_bad 回读不符" >&2; exit 1; }
echo "结果: 补 $n_patch 跳 $n_skip 缺 $n_missing; 全部回读通过 → $IMG"

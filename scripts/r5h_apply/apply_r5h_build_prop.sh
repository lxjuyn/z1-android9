#!/bin/bash
# apply_r5h_build_prop.sh — r5h 轮 build.prop perf 3 键追加（幂等）
#
# 判定出处: survey.perfFiles（2026-10-30）与 HANDOFF_20261029.md P0-B 兜底节:
#   进: debug.sf.nobootanimation=1   纯收益（/bin/bootanimation 内含该串，已验）
#       dalvik.vm.dex2oat-threads=2  ramdisk default.prop 现值=1，双核应=2
#       ro.lmk.debug=false           仅关 lmkd 日志，无害
#   不进: pm.dexopt.disable_bg_dexopt=true（副作用真实: 新装 app 冷启动走 JIT;
#         default.prop 现值 pm.dexopt.bg-dexopt=speed-profile，风暴本来就轻）
#   z1_perf.rc / z1_perf_tune.sh / 服务停用不在本脚本范围（P0-B 步骤 5:
#   先 GPU 修复达标后再叠加，单变量验证）。
#
# 用法:   sudo bash apply_r5h_build_prop.sh <system.img>
# 纪律:   拒绝四件只读基线; marker 幂等; 写完 grep 回读断言; umount 后 ro 复读。
set -euo pipefail

BASELINE=(
  /home/lxj/claude/6572/out/z1_r5_system/system.img.r5f_final
  /home/lxj/claude/6572/out/z1_r5_system/system.img.r5_final
  /home/lxj/claude/6572/out/z1_r5c_boot/boot.img
  /home/lxj/claude/6572/out/z1_r41_final/boot.img
)
MARKER="# === r5h perf append"
BLOCK='# === r5h perf append (2026-10-30, survey.perfFiles 判定; pm.dexopt.disable_bg_dexopt 有意不加入: 新装 app 冷启动走 JIT) ===
debug.sf.nobootanimation=1
dalvik.vm.dex2oat-threads=2
ro.lmk.debug=false'

[ $# -eq 1 ] || { echo "用法: sudo bash $0 <system.img>" >&2; exit 2; }
[ "$(id -u)" -eq 0 ] || { echo "需要 root (mount)" >&2; exit 2; }
IMG=$(realpath "$1"); [ -f "$IMG" ] || { echo "镜像不存在: $IMG" >&2; exit 2; }
for b in "${BASELINE[@]}"; do
  [ "$IMG" != "$(realpath -m "$b")" ] || { echo "拒绝: $IMG 是只读基线" >&2; exit 1; }
done
AV=$(df -k / | awk 'NR==2{print $4}'); [ "$AV" -ge 1228800 ] || { echo "磁盘不足 ${AV}K < 1.2G" >&2; exit 1; }

MP=$(mktemp -d /tmp/r5h_bp.XXXXXX)
cleanup() { mountpoint -q "$MP" && umount "$MP" || true; rmdir "$MP" 2>/dev/null || true; }
trap cleanup EXIT
mount -o loop,rw "$IMG" "$MP"

if grep -q "^${MARKER}" "$MP/build.prop"; then
  echo "marker 已存在, 幂等跳过"
else
  printf '\n%s\n' "$BLOCK" >> "$MP/build.prop"
  sync
fi
echo "--- 回读断言 ---"
grep -nE 'nobootanimation|dex2oat-threads=|ro\.lmk\.debug|disable_bg_dexopt|hardware\.egl' "$MP/build.prop"
n=$(grep -cE '^debug\.sf\.nobootanimation=1$|^dalvik\.vm\.dex2oat-threads=2$|^ro\.lmk\.debug=false$' "$MP/build.prop")
[ "$n" -eq 3 ] || { echo "FATAL: 3 键回读只得 $n" >&2; umount "$MP"; exit 1; }
grep -q '^pm\.dexopt\.disable_bg_dexopt=true$' "$MP/build.prop" && \
  { echo "FATAL: disable_bg_dexopt 不应存在" >&2; umount "$MP"; exit 1; }
umount "$MP"
echo "OK → $IMG"

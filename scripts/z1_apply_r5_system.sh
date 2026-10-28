#!/bin/bash
# R5 system 手术脚本（2026-10-28 定稿，与 out/z1_r5_stage/system.img.r5patched 上的
# 实际操作逐条一致，已验证）。用法: apply_r5_system.sh [system.img]
# 内容: F1 音频 lib 前缀 HAL + BT 三死文件清理。
# 注意: debugfs 对不存在文件的 stat 也返回退出码 0——核验一律用 ls/dump 内容，勿信 rc。
set -euo pipefail
IMG="${1:-/home/lxj/claude/6572/out/z1_r3f_system/system.img}"
BK="/home/lxj/claude/6572/bridge_backups/pre_r5_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BK"; cp -a "$IMG" "$BK/system.img.pre_r5"
echo "[backup] $BK/system.img.pre_r5"

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

# F1: lib 前缀音频 HAL（shim 的 dlopen 目标）
debugfs -R 'dump /vendor/lib/hw/audio.primary.default.so '"$TMP"'/audio_default.so' "$IMG" >/dev/null 2>&1
debugfs -w -R 'write '"$TMP"'/audio_default.so /vendor/lib/libaudio.primary.default.so' "$IMG" >/dev/null
INO=$(debugfs -R 'stat /vendor/lib/libaudio.primary.default.so' "$IMG" 2>/dev/null | grep -oP 'Inode: \K[0-9]+' | head -1)
debugfs -w -R "set_inode_field <$INO> mode 0100644" "$IMG" >/dev/null
debugfs -R 'dump /vendor/lib/libaudio.primary.default.so '"$TMP"'/f1_verify.so' "$IMG" >/dev/null
MD5_SRC=$(md5sum "$TMP/audio_default.so" | cut -d' ' -f1)
MD5_RD=$(md5sum "$TMP/f1_verify.so" | cut -d' ' -f1)
[ "$MD5_SRC" = "$MD5_RD" ] || { echo "F1 verify FAIL"; exit 1; }
debugfs -R 'stat /vendor/lib/libaudio.primary.default.so' "$IMG" 2>/dev/null | grep -q 'Type: regular' || { echo "F1 type FAIL"; exit 1; }
echo "[F1] /vendor/lib/libaudio.primary.default.so OK (md5 $MD5_RD)"

# BT 三死文件清理（先断言在场，删后用 ls 复核）
for f in /lib/bt14_bluetooth.default.so /lib/android.hardware.bluetooth@1.0-impl.so /lib/libbtcust.so; do
    debugfs -R "stat $f" "$IMG" 2>/dev/null | grep -q 'Inode' || { echo "[BT] $f 不在场，跳过"; continue; }
    debugfs -w -R "rm $f" "$IMG" >/dev/null 2>&1
    debugfs -R 'ls -l /lib' "$IMG" 2>/dev/null | grep -qF "$(basename "$f")" && { echo "[BT] $f 删除失败"; exit 1; }
    echo "[BT] $f 已删除"
done
echo "[done] R5 system 手术完成: $IMG"

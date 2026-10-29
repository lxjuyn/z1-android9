#!/bin/bash
# apply_wifi_emi_fix.sh — 修 Z1 WiFi 的 EMI mapping fail（回归修复）
#
# 背景（见 out/WMT_EMI_RULING.md）:
#   R5 交付件里的 mtk_stp_wmt_soc.ko 用的是**上游 A 版** EMI 基址 0x80080000，
#   该地址落在内核管理的 RAM 里（非 no-map），导致
#   arch/arm/mm/ioremap.c:318 的
#       WARN_ON(memblock_is_map_memory(...) && mtype != MT_MEMORY_RW)
#   触发、ioremap 返回 NULL，驱动打印 "EMI mapping fail"。
#   WiFi 因此完全不可用（固件从未下载，wlan0 不出现）。
#
#   而 **2026-08-14 已在 Z1 上跑通过** 的版本用的是 B 版基址 0xbe080000，
#   该地址落在已交付 dtb 的 connsys@be000000 no-map carveout 内 —— 物理上正确。
#   完整成功日志: mainline_recon/z1_boot_mainline_20260814_202853.log
#     Open FW image done, 160464 bytes -> WMT turn on WIFI success!
#     -> wlan0 -> iw scan 扫到 12 个真实 AP
#
#   即 R5 把那次修复**回退**了。本脚本换回 B 版，**不需要动 dtb**。
#
# 判据提醒: 成功时**不会**打印 "EMI mapping OK"（debug 级被编译掉）。
#           上板判据是"没有 EMI mapping fail 行"。
#
# usage: apply_wifi_emi_fix.sh <in.img> <out.img>
set -eu
IN=${1:?in.img}; OUT=${2:?out.img}
D=$(cd "$(dirname "$0")" && pwd)
[ "$IN" != "$OUT" ] || { echo "refusing in-place edit" >&2; exit 1; }
cp -a "$IN" "$OUT"

KO=$D/mtk_stp_wmt_soc.ko.B_emi_0xbe080000
[ -f "$KO" ] || { echo "missing $KO" >&2; exit 1; }

# 先断言这枚确实是 B 版（0xbe08 = 48648），避免误装 A 版
if ! arm-linux-gnueabihf-objdump -d "$KO" 2>/dev/null | grep -qE 'movt\s+r0, #48648'; then
    echo "ERROR: $KO 不含 B 版基址 0xbe080000 (movt r0,#48648)" >&2; exit 1
fi

echo "换入 B 版 mtk_stp_wmt_soc.ko"
debugfs -w -R "rm /lib/modules/z1-radio/mtk_stp_wmt_soc.ko" "$OUT" >/dev/null 2>&1 || true
debugfs -w -R "write $KO /lib/modules/z1-radio/mtk_stp_wmt_soc.ko" "$OUT" >/dev/null 2>&1
for a in "mode 0100644" "uid 0" "gid 0"; do
    debugfs -w -R "sif /lib/modules/z1-radio/mtk_stp_wmt_soc.ko $a" "$OUT" >/dev/null 2>&1
done

echo "done -> $OUT"
echo "verify: e2fsck -fn $OUT"

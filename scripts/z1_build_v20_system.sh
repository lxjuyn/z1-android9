#!/bin/bash
# z1_build_v20_system.sh — v20 = v17 + 音频 5 个 .ko 换成树内新构建
#
# 为什么: 镜像 /lib/modules 里这 5 个模块是**旧构建**（和 z1_cst836u.ko 同一类问题），
# 证据是字符串：镜像版 snd-soc-mt6323.ko 里 "MIC Input/MIC UL/MIC1/MIC2/AIF1 Capture"
# 命中 0，树内版命中 6；mt6572-afe-pcm.ko 里 "AWB Source/MIC Uplink/MIC" 镜像 0、树内 2。
# 也就是说镜像里的音频栈**不含采集(MIC)路径**，录音不可能工作。
# 五个模块 vermagic 都是 7.0.0-rc7-z1+，与内核匹配。
set -euo pipefail
cd /home/lxj/claude/6572
SRC=out/z1_v17_system/system_v17.img
DST=out/z1_v20_audio_system/system_v20.img
M=fork_linux/sound/soc
[ -f "$M/codecs/snd-soc-mt6323.ko" ] || { echo "ERR: 树内音频模块未构建"; exit 1; }
mkdir -p "$(dirname "$DST")"
tools/z1_make_system_image.sh "$SRC" "$DST" \
    /lib/modules/snd-soc-mt6323.ko:"$M/codecs/snd-soc-mt6323.ko":0100664 \
    /lib/modules/mt6323-accdet.ko:"$M/codecs/mt6323-accdet.ko":0100664 \
    /lib/modules/snd-soc-simple-amplifier.ko:"$M/codecs/snd-soc-simple-amplifier.ko":0100664 \
    /lib/modules/mt6572-afe-pcm.ko:"$M/mediatek/mt6572/mt6572-afe-pcm.ko":0100664 \
    /lib/modules/mt6572-mt6323.ko:"$M/mediatek/mt6572/mt6572-mt6323.ko":0100664

for f in snd-soc-mt6323 mt6323-accdet snd-soc-simple-amplifier mt6572-afe-pcm mt6572-mt6323; do
    debugfs -w -R "ea_set /lib/modules/$f.ko security.selinux u:object_r:system_file:s0" "$DST" >/dev/null 2>&1
done
echo "--- 判定: 每个模块的 MIC 字符串数(镜像必须与树内一致) ---"
for f in snd-soc-mt6323 mt6572-afe-pcm; do
    tmp=$(mktemp); rm -f "$tmp"
    debugfs -R "dump /lib/modules/$f.ko $tmp" "$DST" >/dev/null 2>&1
    printf '%-22s %s\n' "$f" "$(strings -a "$tmp" | grep -cE 'MIC Input|MIC UL|MIC1|MIC2|AIF1 Capture|AWB Source|MIC Uplink')"
    rm -f "$tmp"
done
echo "整镜像 sha256 = $(sha256sum "$DST" | cut -d' ' -f1)"

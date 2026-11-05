#!/bin/bash
# z1_flash_chunked.sh — 分块 + 断点续写地刷单个分区
#
# 为什么需要它: 这台 Z1 与 PC 的 USB 链路不稳, 一次写 943MB 常在 30%~87% 处
# 报 "Data ack failed for sdmmc_write_data" / USBError(5), 而 flash_with_retry.sh
# 每次失败都要"整块重擦重写", 于是一晚上只能前进几成。
# 这里改成: 每块 chunk_mb 兆单独写, 失败只重试这一块, 成功的块记进状态文件,
# 重跑脚本会从断点继续。用的底层命令是 mtkclient 的 `wo`(按字节偏移写 flash)。
#
# 用法: tools/z1_flash_chunked.sh <partition> <image> [chunk_mb] [maxtries]
#   例: tools/z1_flash_chunked.sh android out/z1_v18_gpu_system/system_v18.img 48 6
#
# 状态文件: /tmp/z1chunk_<partition>_<image sha 前12>.state
# 冲突提示: 同一分区不要同时跑两个写入。
set -u
DIR=/home/lxj/claude/6572/mtkclient_v2141
PY=/home/lxj/claude/6572/mtkclient_venv/bin/python
PART=${1:?partition}; IMG=${2:?image}; CHUNK_MB=${3:-48}; TRIES=${4:-6}

case "$PART" in
  preloader) OFF=0x0;        SIZE=0x0C00000 ;;
  pro_info)  OFF=0xD00000;   SIZE=0x300000 ;;
  nvram)     OFF=0x1000000;  SIZE=0x500000 ;;
  seccfg)    OFF=0x2900000;  SIZE=0x20000 ;;
  uboot)     OFF=0x2920000;  SIZE=0x60000 ;;
  bootimg)   OFF=0x2980000;  SIZE=0x600000 ;;
  recovery)  OFF=0x2F80000;  SIZE=0x600000 ;;
  misc)      OFF=0x35C0000;  SIZE=0x80000 ;;
  logo)      OFF=0x3640000;  SIZE=0x300000 ;;
  android)   OFF=0x4340000;  SIZE=0x38400000 ;;
  cache)     OFF=0x3C740000; SIZE=0xE200000 ;;
  usrdata)   OFF=0x4A940000; SIZE=0x9DDC0000 ;;
  *) echo "ERR: 未知分区 $PART (把它的起始/大小加进本脚本的表里)" >&2; exit 1 ;;
esac

# 后面会 cd 到 mtkclient 目录，先把镜像路径转绝对
IMG=$(cd "$(dirname "$IMG")" 2>/dev/null && echo "$(pwd)/$(basename "$IMG")")
[ -f "$IMG" ] || { echo "ERR: $IMG 不存在" >&2; exit 1; }
IMG_SIZE=$(stat -c%s "$IMG")
OFF_D=$((OFF)); SIZE_D=$((SIZE))
if [ "$IMG_SIZE" -gt "$SIZE_D" ]; then
  echo "ERR: 镜像 $IMG_SIZE B 超过分区大小 $SIZE_D B" >&2; exit 1
fi

SHA=$(sha256sum "$IMG" | cut -c1-12)
STATE=/tmp/z1chunk_${PART}_${SHA}.state
CHUNK=$((CHUNK_MB * 1024 * 1024))
TOTAL=$(( (IMG_SIZE + CHUNK - 1) / CHUNK ))

echo "=========================================="
echo " 分块刷写: $PART  <- $IMG"
echo " 分区字节偏移: $(printf '0x%x' $OFF_D)  大小: $(printf '0x%x' $SIZE_D)"
echo " 镜像: $IMG_SIZE B  块: ${CHUNK_MB}MB x $TOTAL  每块最多重试 $TRIES"
echo " 状态: $STATE"
echo "=========================================="

touch "$STATE"
cd "$DIR" || exit 1

fail=0
for ((i=0; i<TOTAL; i++)); do
  if grep -qx "$i" "$STATE" 2>/dev/null; then
    continue
  fi
  start=$((i * CHUNK))
  len=$(( IMG_SIZE - start )); [ "$len" -gt "$CHUNK" ] && len=$CHUNK
  hoff=$(printf '0x%x' $((OFF_D + start)))
  hlen=$(printf '0x%x' "$len")
  tmp=/tmp/z1chunk_${PART}_${SHA}.bin
  tail -c +$((start + 1)) "$IMG" | head -c "$len" > "$tmp"
  got=$(stat -c%s "$tmp" 2>/dev/null || echo 0)
  if [ "$got" != "$len" ]; then echo "ERR: 取块 $i 失败 ($got != $len)"; exit 1; fi

  ok=0
  for ((t=1; t<=TRIES; t++)); do
    out=$(timeout 300 "$PY" mtk.py wo "$hoff" "$hlen" "$tmp" 2>&1)
    rc=$?
    if echo "$out" | grep -q "Wrote .* to offset .* with length" && ! echo "$out" | grep -qiE "USBError|Data ack failed|Failed to write|Traceback"; then
      echo "$i" >> "$STATE"
      ok=1
      echo "  [块 $((i+1))/$TOTAL] $hoff +$hlen  OK"
      break
    fi
    echo "  [块 $((i+1))/$TOTAL] 第 $t 次失败 (rc=$rc): $(echo "$out" | grep -iE 'USBError|Data ack failed|Failed to write' | head -1)"
    sleep 3
  done
  rm -f "$tmp"
  if [ "$ok" -ne 1 ]; then
    fail=1
    echo "块 $i 连续 $TRIES 次失败 —— 停止。检查线/换 USB 口后重跑本脚本，会从这里继续。"
    break
  fi
done

if [ "$fail" -eq 0 ] && [ "$(grep -c . "$STATE")" -eq "$TOTAL" ]; then
  echo "全部 $TOTAL 块写完。"
  echo "（未做整分区读回校验；需要严格校验请跑 tools/flash_with_retry.sh 的校验段或手工 mtk.py r）"
  exit 0
fi
exit 2

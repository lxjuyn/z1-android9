#!/bin/bash
# z1_verify_chunks.sh — 分块读回 + 逐块 cmp 校验（配套 z1_flash_chunked.sh）
# 用法: tools/z1_verify_chunks.sh <partition> <image> [chunk_mb] [maxtries]
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
  *) echo "ERR: 未知分区 $PART" >&2; exit 1 ;;
esac
IMG=$(cd "$(dirname "$IMG")" 2>/dev/null && echo "$(pwd)/$(basename "$IMG")")
[ -f "$IMG" ] || { echo "ERR: $IMG 不存在" >&2; exit 1; }
IMG_SIZE=$(stat -c%s "$IMG"); OFF_D=$((OFF)); SIZE_D=$((SIZE))
SHA=$(sha256sum "$IMG" | cut -c1-12)
CHUNK=$((CHUNK_MB * 1024 * 1024))
TOTAL=$(( (IMG_SIZE + CHUNK - 1) / CHUNK ))
RD=/tmp/z1rd_${PART}_${SHA}.bin
echo "=========================================="
echo " 读回校验: $PART  <- $IMG"
echo " 镜像: $IMG_SIZE B  块: ${CHUNK_MB}MB x $TOTAL"
echo "=========================================="
cd "$DIR" || exit 1
BAD=0
for ((i=0; i<TOTAL; i++)); do
  start=$((i * CHUNK)); len=$(( IMG_SIZE - start )); [ "$len" -gt "$CHUNK" ] && len=$CHUNK
  hoff=$(printf '0x%x' $((OFF_D + start))); hlen=$(printf '0x%x' "$len")
  ok=0
  for ((t=1; t<=TRIES; t++)); do
    rm -f "$RD"
    out=$(timeout 300 "$PY" mtk.py ro "$hoff" "$hlen" "$RD" 2>&1); rc=$?
    got=$(stat -c%s "$RD" 2>/dev/null || echo 0)
    if [ "$got" = "$len" ] && ! echo "$out" | grep -qiE "USBError|Data ack failed|Traceback"; then ok=1; break; fi
    echo "  [块 $((i+1))/$TOTAL] 第 $t 次读失败 (rc=$rc got=$got): $(echo "$out" | grep -iE 'USBError|Data ack failed|Traceback|error' | head -1)"
    sleep 3
  done
  if [ "$ok" -ne 1 ]; then echo "  [块 $((i+1))/$TOTAL] 读回连续失败 —— 停止"; BAD=$((BAD+1)); break; fi
  if cmp -s <(tail -c +$((start+1)) "$IMG" | head -c "$len") "$RD"; then
    echo "  [块 $((i+1))/$TOTAL] $hoff +$hlen  一致 ✅"
  else
    echo "  [块 $((i+1))/$TOTAL] $hoff +$hlen  不一致 ❌"
    cmp <(tail -c +$((start+1)) "$IMG" | head -c "$len") "$RD" | head -3
    BAD=$((BAD+1))
  fi
done
rm -f "$RD"
if [ "$BAD" -eq 0 ]; then echo "全部 $TOTAL 块读回逐字节一致 ✅"; exit 0; fi
echo "有 $BAD 块不一致/失败 ❌"; exit 2

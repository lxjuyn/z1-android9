#!/bin/bash
# flash_with_retry.sh — 无人值守安全刷写单个分区(反复重试 + 失败回滚兜底)
#
# 设计目标:
#   - mtkclient 对老 MT6572 写大分区偶发半写(usb bulk 丢包)。
#   - 此脚本:最多重试 N 次,每次趁 preloader/BROM 窗口抓一次写。
#   - 检测到可能半写/半擦时尝试回滚；回滚也可能失败，不保证恢复。
#
# 用法: flash_with_retry.sh <partition> <image> [rollback_image] [maxtries]
#   partition       GPT 真名: uboot / recovery / bootimg
#   image           要刷入的镜像
#   rollback_image  失败后回滚用的镜像(默认 = image 本身,即不回滚)
#   maxtries        最大重试次数(默认 8)
#
# 退出码:
#   0 = 写命令确认完成 (Z1_VERIFY=0 时未做读回校验)
#   1 = 参数错
#   2 = 全部重试用尽
#   3 = 恢复失败或没有回滚镜像(可能半写/半擦,需人工)
#   4 = 写命令确认完成,但请求的读回校验不可用
#
# 重要: uboot(bootloader)半写=变砖风险。写失败只"尝试"回滚;回滚命令也可能失败,
# 或内容未被读回验证。uboot 半写到一半时设备可能已起不来,无 adb 救。
# 因此 uboot 这种 bootloader 分区,默认 rollback_image 必须传(且是可靠的现役原版)。
set -u
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd) || exit 1
source "$SCRIPT_DIR/flash_result_parser.sh" || exit 1
DIR=/home/lxj/claude/6572/mtkclient_v2141
PY=/home/lxj/claude/6572/mtkclient_venv/bin/python
PART="${1:-}"
IMG_ARG="${2:-}"
ROLLBACK_ARG="${3:-}"
MAXTRIES="${4:-8}"

# set -u 下防 integer expression: maxtries 必须是纯数字
[[ "$MAXTRIES" =~ ^[1-9][0-9]*$ ]] || {
  echo "ERROR: maxtries 必须是正整数: '$MAXTRIES'" >&2
  exit 1
}

[ -n "$PART" ] && [ -n "$IMG_ARG" ] || {
  echo "usage: $0 <partition> <image> [rollback_image] [maxtries]"
  echo "  例: $0 uboot /home/lxj/claude/6572/out/lk_Z1.bin           # 写 lk(无回滚)"
  echo "  例: $0 recovery /home/lxj/claude/6572/out/twrp.img /home/lxj/claude/6572/out/live_recovery.img"
  exit 1
}

# 镜像路径支持相对(相对当前 cwd)和绝对,统一转绝对,避免 cd mtkclient 后失效
abs_img=$(cd "$(dirname "$IMG_ARG")" 2>/dev/null && echo "$(pwd)/$(basename "$IMG_ARG")")
abs_rollback=""
if [ -n "$ROLLBACK_ARG" ]; then
  abs_rollback=$(cd "$(dirname "$ROLLBACK_ARG")" 2>/dev/null && echo "$(pwd)/$(basename "$ROLLBACK_ARG")")
fi
IMG="$abs_img"
ROLLBACK="$abs_rollback"
VERIFY_FILE=$(mktemp /tmp/z1_verify_part.XXXXXX.bin) || exit 1
trap 'rm -f "$VERIFY_FILE"' EXIT

cd "$DIR" || exit 1

# bootloader 类分区强制要求回滚镜像(变砖防护)
case "$PART" in
  uboot|preloader|lk)
    [ -n "$ROLLBACK" ] || {
      echo "ERROR: 写 $PART 是 bootloader,必须提供 rollback_image(现役原版备份)防变砖"
      exit 1
    }
    ;;
esac

[ -f "$IMG" ] || { echo "ERROR: $IMG 不存在"; exit 1; }
[ -z "$ROLLBACK" ] || [ -f "$ROLLBACK" ] || { echo "ERROR: rollback $ROLLBACK 不存在"; exit 1; }

IMG_SIZE=$(stat -c%s "$IMG" 2>/dev/null)
echo "=========================================="
echo " 刷写: $PART  ← $IMG ($IMG_SIZE B)"
[ -n "$ROLLBACK" ] && echo " 回滚: $ROLLBACK"
echo " 最大重试: $MAXTRIES 次"
echo "=========================================="

verify_partition_prefix() {
  local expected="$1"
  local label="$2"
  local verify_out verify_rc verify_size expected_size

  : > "$VERIFY_FILE"
  # timeout 60: 防止 mtk.py r 在设备退出 preloader(内核常驻)后永久轮询挂死
  verify_out=$(timeout 60 "$PY" mtk.py r "$PART" "$VERIFY_FILE" 2>&1)
  verify_rc=$?
  echo "$verify_out" | tail -8

  if [ "$verify_rc" -ne 0 ] || mtk_output_failed "$verify_out" || [ ! -f "$VERIFY_FILE" ]; then
    if ! mtk_output_connected "$verify_out"; then
      # 完全没连上(设备不在 preloader, 或 60s 超时无设备): 不是半写证据
      echo "$label 读回未连上设备(rc=$verify_rc), 内容尚未验证"
      return 2
    fi
    echo "$label 读回失败(rc=$verify_rc)"
    return 1
  fi

  verify_size=$(stat -c%s "$VERIFY_FILE" 2>/dev/null || echo 0)
  expected_size=$(stat -c%s "$expected" 2>/dev/null || echo 0)
  if [ "$verify_size" -lt "$expected_size" ]; then
    echo "$label 读回过短: $verify_size B < $expected_size B"
    return 1
  fi

  if ! cmp -s -n "$expected_size" "$VERIFY_FILE" "$expected"; then
    echo "$label 读回前 $expected_size B 与期望镜像不一致"
    return 1
  fi

  echo "$label 完整分区读回完成，前 $expected_size B 与期望镜像一致"
  return 0
}

erase_partition() {
  # Erase the target partition before writing to clear stale/stuck blocks.
  # This fixes XMC eMMC preloader read failures (SD0 DAT TMO) caused by
  # dirty blocks left from prior writes or partial writes.
  # Returns: 0=erase succeeded or skipped (no device), 1=erase failed after connection
  local erase_out erase_rc
  ERASE_CONFIRMED=0
  echo "--- 预擦除: mtk.py e $PART ---"
  erase_out=$(timeout 120 "$PY" mtk.py e "$PART" 2>&1)
  erase_rc=$?
  echo "$erase_out" | tail -5

  if mtk_erase_succeeded "$erase_out" "$erase_rc"; then
    ERASE_CONFIRMED=1
    echo "擦除完成: $PART"
    return 0
  fi

  # 擦除失败: 只有当"擦除中途确曾建立连接"时才视为真正的擦除错误
  # (设备在 preloader,擦除失败可能损坏分区结构,需回滚)
  # 干净没连上(设备不在 preloader) → 擦除跳过,继续写(写命令本身也会面对同样窗口问题)
  if mtk_output_connected "$erase_out"; then
    echo "擦除失败(rc=$erase_rc, 已连接或状态未知)"
    return 1
  fi

  echo "擦除未与设备建立连接(设备不在 preloader),跳过擦除,继续写入"
  return 0
}

rollback_and_verify() {
  local rollback_out rollback_rc vrc

  [ -n "$ROLLBACK" ] || return 1
  echo "=== 回滚:写回 $ROLLBACK → $PART ==="
  rollback_out=$("$PY" mtk.py w "$PART" "$ROLLBACK" 2>&1)
  rollback_rc=$?
  echo "$rollback_out" | tail -8
  if ! mtk_write_succeeded "$rollback_out" "$rollback_rc" "$ROLLBACK"; then
    echo "回滚写入失败(rc=$rollback_rc)"
    return 1
  fi

  if [ "${Z1_VERIFY:-1}" = "0" ]; then
    echo "回滚写命令已确认完成；Z1_VERIFY=0，未做读回校验"
    return 0
  fi

  echo "=== 回滚校验:完整读回 $PART 并比较回滚镜像 ==="
  verify_partition_prefix "$ROLLBACK" "回滚"
  vrc=$?
  if [ "$vrc" -eq 2 ]; then
    # 命令确认完成不等于已读回验证；上层不得声称读回一致。
    echo "回滚写命令已确认完成；读回不可用，恢复内容尚未验证"
    return 0
  fi
  return "$vrc"
}

try=1
while [ "$try" -le "$MAXTRIES" ]; do
  echo ""
  echo ">>> 第 $try/$MAXTRIES 次尝试写 $PART @ $(date '+%H:%M:%S' 2>/dev/null || echo '?')"

  # --- 预擦除: 清除脏块,修复 XMC eMMC SD0 DAT TMO ---
  if ! erase_partition; then
    echo "擦除失败(已连接或连接状态未知),可能已半擦除,先尝试回滚"
    if [ -n "$ROLLBACK" ]; then
      if rollback_and_verify; then
        echo "回滚命令已确认完成(擦除失败后)，读回状态见上方；等待下一次重试"
      else
        echo "擦除失败且回滚失败;停止,禁止继续自动操作"
        exit 3
      fi
    else
      echo "无回滚镜像且可能已半擦除，停止自动操作"
      exit 3
    fi
    try=$((try+1))
    echo "--- 等 8s 让设备/USB 复位 ---"
    sleep 8
    continue
  fi

  # mtk w 自己会轮询等待窗口；此处尚无写操作超时，见限制。
  OUT=$("$PY" mtk.py w "$PART" "$IMG" 2>&1)
  RC=$?
  echo "$OUT" | tail -8

  # rc=0 不够：禁止失败标记，且末行必须精确确认本镜像写完。
  if mtk_write_succeeded "$OUT" "$RC" "$IMG"; then
    echo ""
    echo "第 $try 次写命令完成: $PART ← $IMG"

    # 明确区分命令确认完成和实际读回校验。
    if [ "${Z1_VERIFY:-1}" = "0" ]; then
      echo "Z1_VERIFY=0: 写命令已确认完成；未做读回校验，不能声称镜像已读回验证"
      exit 0
    fi

    echo "=== 严格校验:完整读回 $PART 并比较目标镜像长度 ==="
    verify_partition_prefix "$IMG" "目标"
    vrc=$?
    if [ "$vrc" -eq 0 ]; then
      echo "第 $try 次写入及严格读回校验均成功"
      exit 0
    fi

    if [ "$vrc" -eq 2 ]; then
      # 不因校验不可用自动破坏命令已确认完成的镜像，但返回独立状态。
      echo "第 $try 次写命令已确认完成；读回不可用，内容未验证；未自动回滚"
      exit 4
    fi

    echo "写命令返回成功，但严格读回校验失败；本次不得判定成功"
    if [ -n "$ROLLBACK" ]; then
      if rollback_and_verify; then
        echo "回滚命令已确认完成，读回状态见上方；等待下一次目标写入"
      else
        echo "严格回滚失败；停止，禁止继续自动写入"
        exit 3
      fi
    else
      echo "无回滚镜像，停止"
      exit 3
    fi
    try=$((try+1))
    echo "--- 等 8s 让设备/USB 复位 ---"
    sleep 8
    continue
  fi

  echo "第 $try 次写失败(rc=$RC)。$([ -n "$ROLLBACK" ] && echo '将检查是否需回滚' || echo '无回滚镜像,将检查是否必须停止')"

  # 写失败：已擦除、传输过或状态未知均需恢复。只有明确未连接且未擦除才可跳过回滚。
  if [ "$ERASE_CONFIRMED" -eq 1 ] || mtk_output_connected "$OUT"; then
    if [ -z "$ROLLBACK" ]; then
      echo "可能已半写/半擦且无回滚镜像，停止自动操作"
      exit 3
    fi
    if rollback_and_verify; then
      echo "回滚命令已确认完成，读回状态见上方；可继续重试"
    else
      echo "回滚写入或读回校验失败；停止，禁止继续自动写入"
      exit 3
    fi
  elif [ -n "$ROLLBACK" ]; then
    echo "本次写命令未与设备建立连接(干净失败), 不回滚, 等待窗口重试"
  fi

  try=$((try+1))
  # 如果设备已离线(写失败常伴随 USB 断),等设备重新暴露窗口
  echo "--- 等 8s 让设备/USB 复位 ---"
  sleep 8
done

echo ""
echo "全部 $MAXTRIES 次尝试用尽,$PART 仍未写成功"
if [ -n "$ROLLBACK" ]; then
  echo "已尝试回滚。建议:物理重启设备,检查 USB,换更大 retry 数再试。"
fi
exit 2

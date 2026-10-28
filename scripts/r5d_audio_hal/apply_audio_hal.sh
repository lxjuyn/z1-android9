#!/usr/bin/env bash
#
# apply_audio_hal.sh — 幂等、非原地地把一个 Android 9 primary audio HAL 部署进 Z1 的 system.img。
#
# 作者: 音频 HAL 移植 agent (2026-10-28)
# 依据: out/AUDIO_HAL_PORT.md 的阶段 1 结论 —— LOS14(Android7) 的 MTK 真 HAL 依赖
#       /dev/eac 等 MTK 私有内核接口，在本 mainline 内核上不可用；应部署一个基于
#       tinyalsa 的自建 v2.0 primary HAL (= scripts/r5d_audio_hal/audio_primary_z1_poc.c)。
#
# 用法:
#   apply_audio_hal.sh [--dry-run] [--hal <path>] <in.img> <out.img>
#
#   --hal <path>   要部署的 HAL .so；缺省 = <脚本目录>/libaudio.primary.z1.so
#   --dry-run      只做校验与打印，不写任何东西
#
# 部署目标路径 (镜像内即 system-as-root 根):
#   /vendor/lib/libaudio.primary.default.so    <- 真正的实现 (shim 会 dlopen 它)
#   /vendor/lib/hw/audio.primary.default.so    <- AudioFlinger 先找的 hw/ 路径
#
# 之所以选 vendor 路径: 镜像内 /vendor/lib/hw/audio.primary.mt6572.so 是个 dlopen shim,
# 它 dlopen "libaudio.primary.default.so" (带 lib 前缀)，走 linker 默认搜索路径
# (/system/lib, /vendor/lib)。镜像里没有 /lib/libaudio.primary.default.so，所以放在
# /vendor/lib/ 最直接。
#
# 纪律: 不原地改；先 cp 到 out.img 再改 out.img；只读校验用 e2fsck -fn；逐项回读比对 sha256。
#
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DRY_RUN=0
HAL=""

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=1; shift ;;
        --hal)     HAL="${2:-}"; shift 2 ;;
        -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
        --)        shift; break ;;
        -*)        echo "未知参数: $1" >&2; exit 2 ;;
        *)         break ;;
    esac
done

IN_IMG="${1:-}"; OUT_IMG="${2:-}"
if [ -z "$IN_IMG" ] || [ -z "$OUT_IMG" ]; then
    echo "用法: $0 [--dry-run] [--hal <path>] <in.img> <out.img>" >&2; exit 2
fi
[ -n "$HAL" ] || HAL="$HERE/libaudio.primary.z1.so"

TARGETS=(
    "/vendor/lib/libaudio.primary.default.so"
    "/vendor/lib/hw/audio.primary.default.so"
)
VENDOR_UID=0
VENDOR_GID=2000
MODE=0100644

log()  { printf '[apply_audio_hal] %s\n' "$*"; }
die()  { printf '[apply_audio_hal][FATAL] %s\n' "$*" >&2; exit 1; }

# ---------- 0. 预检 ----------
[ -f "$IN_IMG" ]  || die "输入镜像不存在: $IN_IMG"
[ -f "$HAL" ]     || die "HAL 二进制不存在: $HAL (先编译 scripts/r5d_audio_hal/audio_primary_z1_poc.c)"
command -v debugfs >/dev/null || die "缺 debugfs"
command -v e2fsck >/dev/null || die "缺 e2fsck"

if [ "$IN_IMG" = "$OUT_IMG" ]; then die "拒绝原地修改 (in==out)"; fi

# 本项目反复因盘满导致 debugfs 静默失败 —— 阈值 4G (可用 Z1_MIN_FREE_GIB 覆盖)
MIN_GIB="${Z1_MIN_FREE_GIB:-4}"
AVAIL_K=$(df -Pk "$(dirname "$OUT_IMG")" | awk 'NR==2{print $4}')
log "目标目录可用空间: $((AVAIL_K/1024/1024)) GiB (阈值 ${MIN_GIB} GiB)"
[ "$AVAIL_K" -ge $((MIN_GIB*1024*1024)) ] || die "可用空间 < ${MIN_GIB}GiB (本项目因盘满多次静默失败)，先清理再跑"

IMG_SIZE=$(stat -c%s "$IN_IMG")
HAL_SIZE=$(stat -c%s "$HAL")
HAL_SHA=$(sha256sum "$HAL" | awk '{print $1}')
log "输入镜像: $IN_IMG ($IMG_SIZE B)"
log "HAL 源:   $HAL ($HAL_SIZE B, sha256 $HAL_SHA)"

# HAL 自检: 必须是一个导出 HMI(hw_module_t) 的 ARM32 Android 共享库
file "$HAL" | grep -q 'ELF 32-bit LSB shared object, ARM' || die "HAL 不是 ARM32 ELF 共享库"
if command -v arm-linux-gnueabihf-nm >/dev/null; then
    arm-linux-gnueabihf-nm -D --defined-only "$HAL" | grep -q ' HMI$' \
        || die "HAL 未导出 HMI —— 不是合法的 HAL 模块"
fi

if [ "$DRY_RUN" = "1" ]; then
    log "--- DRY RUN ---"
    for t in "${TARGETS[@]}"; do
        log "将写入: $t"
        if debugfs -R "stat $t" "$IN_IMG" >/dev/null 2>&1; then
            log "   (已存在，将替换)"
        else
            log "   (不存在，将新建)"
        fi
    done
    log "dumpe2fs 校验 PPT: $(dumpe2fs -h "$IN_IMG" 2>/dev/null | awk -F: '/Block count|Block size/{printf "%s ", $2}')"
    log "DRY RUN 结束，未做任何写操作。"
    exit 0
fi

# ---------- 1. 备份 + 复制成 out.img ----------
BK="${IN_IMG}.bak.$(date +%Y%m%d_%H%M%S)"
log "备份输入镜像 -> $BK"
cp --reflink=auto --sparse=always "$IN_IMG" "$BK"
log "复制 -> $OUT_IMG"
cp --reflink=auto --sparse=always "$IN_IMG" "$OUT_IMG"

# ---------- 2. 记录原文件的 SELinux xattr（尽力而为） ----------
declare -A ORIG_XATTR
for t in "${TARGETS[@]}"; do
    if debugfs -R "ea_list $t" "$IN_IMG" 2>/dev/null | grep -qi 'security.selinux\|selinux'; then
        ORIG_XATTR["$t"]="$(debugfs -R "ea_get $t security.selinux" "$IN_IMG" 2>/dev/null | head -1 || true)"
        log "原 $t 有 security.selinux 标签，已记录（将尝试回写）"
    fi
done

# ---------- 3. 写入 ----------
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
cp "$HAL" "$STAGE/hal.so"

for t in "${TARGETS[@]}"; do
    if debugfs -R "stat $t" "$OUT_IMG" >/dev/null 2>&1; then
        log "rm  $t"
        debugfs -w -R "rm $t" "$OUT_IMG" >/dev/null 2>&1 || true
    fi
    d=$(dirname "$t")
    debugfs -R "stat $d" "$OUT_IMG" >/dev/null 2>&1 || { log "mkdir $d"; debugfs -w -R "mkdir $d" "$OUT_IMG" >/dev/null 2>&1 || true; }
    log "write $t"
    debugfs -w -R "write $STAGE/hal.so $t" "$OUT_IMG" >/dev/null
    debugfs -w -R "set_inode_field $t mode $MODE"          "$OUT_IMG" >/dev/null 2>&1 || true
    debugfs -w -R "set_inode_field $t uid $VENDOR_UID"     "$OUT_IMG" >/dev/null 2>&1 || true
    debugfs -w -R "set_inode_field $t gid $VENDOR_GID"     "$OUT_IMG" >/dev/null 2>&1 || true
    # 回写 SELinux 标签（尽力；失败会告警，需人工 restorecon/补 file_contexts）
    if [ -n "${ORIG_XATTR[$t]:-}" ]; then
        debugfs -w -R "ea_set $t security.selinux ${ORIG_XATTR[$t]}" "$OUT_IMG" >/dev/null 2>&1 \
            && log "   selinux 标签已回写" || log "   !! selinux 标签回写失败，上板前需人工补"
    else
        log "   注意: 未记录到原标签（可能是新文件）—— 首次创建的文件可能缺 SELinux 上下文"
    fi
done

# ---------- 4. 校验 ----------
log "e2fsck 修一遍 (写操作后) ..."
e2fsck -fy "$OUT_IMG" >/dev/null 2>&1 || true
log "e2fsck -fn 只读验证 ..."
if e2fsck -fn "$OUT_IMG" >"$STAGE/e2fsck.txt" 2>&1; then
    log "e2fsck -fn: 干净 ✓"
else
    grep -vi 'clean' "$STAGE/e2fsck.txt" | head -20 | sed 's/^/   /' || true
    die "e2fsck -fn 报告问题，见 $STAGE/e2fsck.txt（已保留 out.img 供排查）"
fi

log "逐项回读比对 ..."
for t in "${TARGETS[@]}"; do
    o="$STAGE/$(echo "$t" | tr '/' '_')"
    debugfs -R "dump $t $o" "$OUT_IMG" >/dev/null 2>&1 || die "回读失败: $t"
    got=$(sha256sum "$o" | awk '{print $1}')
    if [ "$got" = "$HAL_SHA" ]; then
        log "   $t  sha256 匹配 ✓ ($(stat -c%s "$o") B)"
    else
        die "   $t  sha256 不匹配! 期望 $HAL_SHA 实得 $got"
    fi
    if command -v arm-linux-gnueabihf-nm >/dev/null; then
        arm-linux-gnueabihf-nm -D --defined-only "$o" | grep -q ' HMI$' \
            && log "   $t  HMI 存在 ✓" || die "   $t  回读后无 HMI!"
    fi
done

OUT_SHA=$(sha256sum "$OUT_IMG" | awk '{print $1}')
log "==============================="
log "完成。产物: $OUT_IMG"
log "  sha256 = $OUT_SHA"
log "  备份   = $BK"
log ""
log "上板验证: 见 out/AUDIO_HAL_PORT.md 的『上板验证步骤』"
log "==============================="

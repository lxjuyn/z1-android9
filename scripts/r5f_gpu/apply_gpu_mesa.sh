#!/usr/bin/env bash
# =============================================================================
# apply_gpu_mesa.sh — 把 Z1 (MT6572) Android 9 system.img 的渲染栈切到
#                     lima/Mali-400 硬件加速（恢复已验证可用的 Mesa 19.1.8 栈）
#
# 背景 / 根因（详见 out/UI_JANK_GPU.md）:
#   当前交付基线 out/z1_r5_system/system.img.r5d_final 里，SurfaceFlinger 退回
#   SwiftShader 纯软件渲染（帧中位 5,112 ms → SystemUI ANR → watchdog 杀 system_server）。
#   三个独立缺陷:
#     A. r5d 的 libEGL_mesa.so 内 DRI 搜索路径编译死为 /usr/local/lib/dri，
#        而该目录在镜像里不存在；lima_dri.so 实际在 /vendor/lib/dri。
#        （对照: 可用的 p12_r2 libGLES_mesa.so 内是 /vendor/lib/dri）
#     B. build.prop 的 ro.hardware.egl=swiftshader
#     C. /vendor/lib/egl/ 只有 libEGL_mesa.so，缺 libGLES_mesa.so；
#        且 r5d 的 libEGL_mesa.so 是 Mesa 24.0.9，与 swiftshader 的 GLES 不混用。
#
#   本脚本不做新发明，而是**恢复** out/A9_flashpkg_p12_r2/system.img 那套
#   已在板上实测出 `SurfaceFlinger: renderer : Mali400 / Mesa 19.1.8` 的文件。
#
# 纪律:
#   - 非原地: 输出必须是新路径，绝不覆盖输入镜像
#   - 幂等: 重复跑结果一致
#   - 可回滚: 支持 --undo，且所有删除/覆盖都先备份进镜像内 /.r5f_gpu_backup/
#   - debugfs 只读挂载输入镜像; 只写输出副本
#
# 用法:
#   ./apply_gpu_mesa.sh <in.img> <out.img>            # 应用
#   ./apply_gpu_mesa.sh <in.img> <out.img> --undo     # 回滚到应用前状态
#
# 例:
#   ./apply_gpu_mesa.sh out/z1_r5_system/system.img.r5d_final \
#                       out/z1_gpu_stage/system.img.r5f_gpu
# =============================================================================
set -euo pipefail

SRC_IMG_DEFAULT="/home/lxj/claude/6572/out/A9_flashpkg_p12_r2/system.img"
BK_DIR="/.r5f_gpu_backup"

die() { echo "错误: $*" >&2; exit 1; }
log() { echo "[r5f_gpu] $*"; }

IN_IMG="${1:-}"; OUT_IMG="${2:-}"; MODE="${3:-apply}"
[ -n "$IN_IMG" ] && [ -n "$OUT_IMG" ] || {
    echo "用法: $0 <in.img> <out.img> [--undo]"; exit 2; }
[ "$MODE" = "--undo" ] || [ "$MODE" = "apply" ] || die "未知模式 $MODE"

command -v debugfs >/dev/null || die "缺 debugfs (e2fsprogs)"

if [ "$IN_IMG" = "$OUT_IMG" ]; then
    die "输入与输出不能相同（本脚本非原地）"
fi
[ -f "$IN_IMG" ] || die "找不到输入镜像 $IN_IMG"

# 源镜像（恢复用的"已知可用"栈）
SRC_IMG="${R5F_SRC_IMG:-$SRC_IMG_DEFAULT}"
[ -f "$SRC_IMG" ] || die "找不到恢复源镜像 $SRC_IMG"

# ---------------------------------------------------------------------------
# 空间检查 —— 本项目反复因盘满导致 debugfs 静默失败，必须先查
# ---------------------------------------------------------------------------
IN_SZ=$(stat -c%s "$IN_IMG")
NEED_MB=$(( IN_SZ / 1024 / 1024 + 64 ))
AVAIL_MB=$(df -Pm "$(dirname "$OUT_IMG")" | awk 'NR==2{print $4}')
[ "$AVAIL_MB" -ge "$NEED_MB" ] || \
    die "磁盘不足: 需要约 ${NEED_MB}MB, 可用 ${AVAIL_MB}MB (先清理再跑)"
log "磁盘 OK: 需要 ${NEED_MB}MB, 可用 ${AVAIL_MB}MB"

# ---------------------------------------------------------------------------
# 拷贝输入 → 输出（绝不原地改）
# ---------------------------------------------------------------------------
if [ "$IN_IMG" = "$OUT_IMG" ]; then
    die "输入与输出不能相同（本脚本非原地）"
fi
log "拷贝 $IN_IMG → $OUT_IMG"
cp -f "$IN_IMG" "$OUT_IMG"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# debugfs 包装: 对输出副本读写, 对源镜像只读
dfo() { debugfs -w -R "$1" "$OUT_IMG" 2>&1 | grep -v '^debugfs ' || true; }
dfr() { debugfs -R "$1" "$SRC_IMG" 2>&1 | grep -v '^debugfs ' || true; }

host_mb() { # 镜像内剩余空间(MB)
    df -Pm "$OUT_IMG" 2>/dev/null | awk 'NR==2{print $4}' || echo 0
}

# 确保镜像内备份目录存在
mkdir_in_img() { dfo "mkdir $1" >/dev/null; }

# 从源镜像提取文件到宿主 /tmp
extract_from_src() { # <src_path> <dst_host_file>
    rm -f "$2"
    debugfs -R "dump $1 $2" "$SRC_IMG" >/dev/null 2>&1 || die "源镜像里没有 $1"
    [ -s "$2" ] || die "从源镜像提取 $1 失败（空文件）"
}

# 往输出镜像里写文件（debugfs write = 覆盖或新建）
write_to_img() { # <host_file> <dst_path>
    dfo "rm $2" >/dev/null
    dfo "write $1 $2" >/dev/null
    # 校验：大小一致。debugfs stat 有两条 "Size:" 行（第二条是 Fragment 的），
    # 取第一条（真正的 st_size）——它在 "Project:" 行的行尾。
    local want got
    want=$(stat -c%s "$1")
    got=$(debugfs -R "stat $2" "$OUT_IMG" 2>/dev/null \
          | awk '/Size:/{print $NF; exit}')
    [ "$got" = "$want" ] || die "写入 $2 后大小不符 (期望 $want 得到 ${got:-空})"
}

# ===========================================================================
# 回滚
# ===========================================================================
if [ "$MODE" = "--undo" ]; then
    log "回滚模式: 从 $BK_DIR 还原"
    debugfs -R "ls $BK_DIR" "$OUT_IMG" 2>/dev/null | grep -q . \
        || die "镜像里没有 $BK_DIR，无法回滚（是否跑过 apply?）"

    # build.prop
    if debugfs -R "stat $BK_DIR/build.prop" "$OUT_IMG" >/dev/null 2>&1; then
        rm -f "$TMP/bp"
        debugfs -R "dump $BK_DIR/build.prop $TMP/bp" "$OUT_IMG" >/dev/null 2>&1
        write_to_img "$TMP/bp" /build.prop
        log "  已还原 /build.prop"
    fi

    # 恢复原先被删除的文件
    for p in vendor/lib/egl/libEGL_mesa.so vendor/lib/dri/lima_dri.so; do
        base="$(echo "$p" | tr '/' '_')"
        if debugfs -R "stat $BK_DIR/$base" "$OUT_IMG" >/dev/null 2>&1; then
            rm -f "$TMP/$base"
            debugfs -R "dump $BK_DIR/$base $TMP/$base" "$OUT_IMG" >/dev/null 2>&1
            write_to_img "$TMP/$base" "/$p"
            log "  已还原 /$p"
        fi
    done

    # 删掉本次新增的文件
    for p in vendor/lib/egl/libGLES_mesa.so vendor/lib/dri/gallium_dri.so \
             vendor/lib/dri/swrast_dri.so vendor/lib/dri/kms_swrast_dri.so; do
        dfo "rm /$p" >/dev/null
    done
    log "  已删除本次新增的 mesa 文件"
    log "回滚完成 → $OUT_IMG"
    log "  !! 注意: 原 lima_dri.so 若是符号链接，debugfs dump/write 会落成普通文件。"
    log "  !! 如需严格还原符号链接，用 --restore-from 直接 cp 原镜像重来。"
    exit 0
fi

# ===========================================================================
# 应用
# ===========================================================================
log "== 备份原文件到镜像内 $BK_DIR =="
mkdir_in_img "$BK_DIR"
# build.prop
rm -f "$TMP/bp_orig"
debugfs -R "dump /build.prop $TMP/bp_orig" "$OUT_IMG" >/dev/null 2>&1 \
    || die "输出镜像里读不到 /build.prop"
write_to_img "$TMP/bp_orig" "$BK_DIR/build.prop"
log "  备份 /build.prop"

for p in vendor/lib/egl/libEGL_mesa.so vendor/lib/dri/lima_dri.so; do
    base="$(echo "$p" | tr '/' '_')"
    if debugfs -R "stat /$p" "$OUT_IMG" >/dev/null 2>&1; then
        rm -f "$TMP/$base"
        debugfs -R "dump /$p $TMP/$base" "$OUT_IMG" >/dev/null 2>&1 || true
        [ -s "$TMP/$base" ] && write_to_img "$TMP/$base" "$BK_DIR/$base" \
            && log "  备份 /$p"
    fi
done

log "== 1) 从恢复源提取已验证可用的文件 =="
extract_from_src /vendor/lib/egl/libGLES_mesa.so      "$TMP/libGLES_mesa.so"
extract_from_src /vendor/lib/dri/gallium_dri.so       "$TMP/gallium_dri.so"
log "  libGLES_mesa.so  $(stat -c%s "$TMP/libGLES_mesa.so") B"
log "  gallium_dri.so   $(stat -c%s "$TMP/gallium_dri.so") B"

# 记录源文件的符号链接目标（p12_r2 里 lima/swrast/kms_swrast 都是 → gallium_dri.so）
LINK_TARGET="gallium_dri.so"

log "== 2) 部署 mesa 文件 =="
# 2a. 删掉 r5d 里不兼容的 mesa 24 版文件
dfo "rm /vendor/lib/egl/libEGL_mesa.so" >/dev/null
dfo "rm /vendor/lib/dri/lima_dri.so"    >/dev/null
log "  已删除不兼容的 libEGL_mesa.so (Mesa 24.0.9) 与 lima_dri.so"

# 2b. 写入 mesa 19.1.8 栈
write_to_img "$TMP/gallium_dri.so"  /vendor/lib/dri/gallium_dri.so
write_to_img "$TMP/libGLES_mesa.so" /vendor/lib/egl/libGLES_mesa.so
log "  已写入 /vendor/lib/dri/gallium_dri.so 与 /vendor/lib/egl/libGLES_mesa.so"

# 2c. lima/swrast/kms_swrast 符号链接（mesa loader 按 <kernel_driver>_dri.so 查找）
for n in lima_dri.so swrast_dri.so kms_swrast_dri.so; do
    dfo "rm /vendor/lib/dri/$n" >/dev/null
    dfo "symlink /vendor/lib/dri/$n $LINK_TARGET" >/dev/null \
        || log "  !! 建符号链接 $n 失败（继续；loader 可能仍能靠 gallium_dri.so 找到）"
done
log "  已建符号链接 lima/swrast/kms_swrast → $LINK_TARGET"

log "== 3) 安全网: 把 libGLES_mesa.so 也放到 /vendor/lib/egl 的常见别名 =="
# 某些 loader 会找 libGLESv2_<tag>.so；有则一并提供（不覆盖已有 swiftshader 名）
for alias in libGLESv2_mesa.so; do
    if ! debugfs -R "stat /vendor/lib/egl/$alias" "$OUT_IMG" >/dev/null 2>&1; then
        dfo "symlink /vendor/lib/egl/$alias libGLES_mesa.so" >/dev/null \
            && log "  已加兼容别名 $alias → libGLES_mesa.so" || true
    fi
done

log "== 4) 改 build.prop: ro.hardware.egl=swiftshader → mesa =="
if grep -q '^ro\.hardware\.egl=mesa$' "$TMP/bp_orig"; then
    log "  已是 mesa，跳过"
else
    sed -E 's/^ro\.hardware\.egl=.*$/ro.hardware.egl=mesa/' "$TMP/bp_orig" > "$TMP/bp_new"
    grep -q '^ro\.hardware\.egl=mesa$' "$TMP/bp_new" \
        || die "build.prop 里没有 ro.hardware.egl 行，需人工确认"
    write_to_img "$TMP/bp_new" /build.prop
    log "  已写入 ro.hardware.egl=mesa"
fi

log "== 5) 校验 =="
echo "--- /vendor/lib/egl ---"
debugfs -R "ls -l /vendor/lib/egl" "$OUT_IMG" 2>/dev/null | grep -v '^debugfs'
echo "--- /vendor/lib/dri ---"
debugfs -R "ls -l /vendor/lib/dri" "$OUT_IMG" 2>/dev/null | grep -v '^debugfs'
echo "--- build.prop egl 行 ---"
rm -f "$TMP/bp_chk"
debugfs -R "dump /build.prop $TMP/bp_chk" "$OUT_IMG" >/dev/null 2>&1
grep -n 'ro\.hardware\.egl' "$TMP/bp_chk" || die "校验失败: build.prop 无 egl 行"

log "完成 → $OUT_IMG"
echo
echo "==================================================================="
echo "下一步（由主会话决定刷写）:"
echo "  1) 记 sha:  sha256sum $OUT_IMG"
echo "  2) 刷完抓 log，看这三行（缺一即失败）:"
echo "       grep -nE 'SurfaceFlinger: (vendor|renderer|version)' <log>"
echo "     期望: vendor: lima / renderer: Mali400 / OpenGL ES 2.0 Mesa 19.1.8"
echo "  3) 验收指标: logcat -d | grep 'Davey! duration' 中位应从 5,112 ms 大降"
echo "  4) 回滚: $0 $OUT_IMG <rollback.img> --undo"
echo "     （注意: 回滚的输入必须是**已应用过**的那个镜像，因为它内含"
echo "       $BK_DIR 备份；用原始未应用的镜像作输入会报'没有备份'）"
echo "==================================================================="

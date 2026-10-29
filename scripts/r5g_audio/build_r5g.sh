#!/usr/bin/env bash
#
# build_r5g.sh — r5g_audio 的 primary audio HAL 交叉编译脚本 (arm-linux-gnueabihf-gcc)
#
# 与 r5d PoC (NDK clang 构建) 的关键差别:
#   * -nostdlib + 显式链接镜像内抽取的 bionic so —— Ubuntu armhf gcc 默认链 glibc,
#     会混入 crt*.o / libc.so.6 的 NEEDED; -nostdlib 后 NEEDED 完全由被链 so 的
#     SONAME 决定 (= libtinyalsa/liblog/libm/libdl/libc)。
#   * -mfloat-abi=softfp —— 镜像库 readelf -A 实测 cortex-a7/VFPv4 且无
#     Tag_ABI_VFP_args (= softfp ABI); Ubuntu gcc 默认 hard 会 ABI 不匹配。
#   * 不链 libgcc —— Ubuntu armhf libgcc.a 是 hard-float 多元库, 静态拉入与 softfp
#     属性冲突; 64 位除法助手 __aeabi_uldivmod 有意由 bionic libc.so 的
#     LIBC_PRIVATE 导出在运行期解析 (设备 libc.so nm -D 实测导出)。
#   * -fno-stack-protector -U_FORTIFY_SOURCE —— 防 Ubuntu gcc 默认防护
#     (__stack_chk_guard/__memcpy_chk 等) 污染 UND 符号表。
#   * -D_TIME_BITS=32 —— glibc 2.39 头默认把 clock_gettime 重定向成 __clock_gettime64
#     (bionic 无此名, 且 timespec 变 16 字节布局会写歪框架的缓冲); 32 位时间还原成
#     bionic arm32 同款 8 字节 timespec + 普通 clock_gettime。
#   * glibc 头仅当声明用; 运行期全部符号由镜像 bionic 提供 (板上实链)。
#
# 用法:  bash build_r5g.sh
#   产物: 本目录 libaudio.primary.z1.so + poc_r5g.o
# 环境变量:
#   Z1_BASELINE_IMG  只读基线 (缺库时从中 debugfs dump, 默认 system.img.r5f_final)
#   Z1_R5G_LIBDIR    链接库缓存目录 (默认 /tmp/r5g_link)
#   CROSS_COMPILE_CC 编译器 (默认 arm-linux-gnueabihf-gcc)
#
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CC="${CROSS_COMPILE_CC:-arm-linux-gnueabihf-gcc}"
LIBDIR="${Z1_R5G_LIBDIR:-/tmp/r5g_link}"
BASELINE="${Z1_BASELINE_IMG:-/home/lxj/claude/6572/out/z1_r5_system/system.img.r5f_final}"
LIBS="libtinyalsa.so liblog.so libm.so libdl.so libc.so"
SRC="$HERE/audio_primary_z1_poc.c"
OUT="$HERE/libaudio.primary.z1.so"

# MT6572 = Cortex-A7; Android ARM32 ABI = softfp + Thumb-2
ARCH="-mcpu=cortex-a7 -mfpu=neon-vfpv4 -mfloat-abi=softfp -mthumb"

# --- 0. 预检 ---
command -v "$CC" >/dev/null || { echo "[build_r5g][FATAL] 找不到编译器 $CC" >&2; exit 1; }
[ -f "$SRC" ] || { echo "[build_r5g][FATAL] 源码不存在: $SRC" >&2; exit 1; }

# --- 1. 链接库: 缺则从只读基线抽取 (debugfs dump 是只读操作, 不碰基线) ---
if [ ! -f "$LIBDIR/libtinyalsa.so" ]; then
    [ -f "$BASELINE" ] || { echo "[build_r5g][FATAL] 基线镜像不存在: $BASELINE (或预置 $LIBDIR)" >&2; exit 1; }
    echo "[build_r5g] 从基线抽取 bionic 库 -> $LIBDIR"
    mkdir -p "$LIBDIR"
    for f in $LIBS; do
        debugfs -R "dump /lib/$f $LIBDIR/$f" "$BASELINE" >/dev/null
        [ -s "$LIBDIR/$f" ] || { echo "[build_r5g][FATAL] 抽取为空: /lib/$f" >&2; exit 1; }
    done
fi
for f in $LIBS; do
    [ -s "$LIBDIR/$f" ] || { echo "[build_r5g][FATAL] 缺链接库: $LIBDIR/$f" >&2; exit 1; }
done

LIBGCC="$($CC -print-libgcc-file-name)"
echo "[build_r5g] CC=$CC  libgcc=$LIBGCC  libdir=$LIBDIR"

# --- 2. 编译 (头文件 = 本目录 inc/ 的 A9 SDK28 头; 含 _Static_assert ABI 护栏) ---
"$CC" -c -fPIC -Os $ARCH \
    -fno-stack-protector -U_FORTIFY_SOURCE -D_TIME_BITS=32 \
    -Wall -Wextra -Wno-unused-parameter \
    -I"$HERE/inc" -o "$HERE/poc_r5g.o" "$SRC"

# --- 3. 链接: --no-undefined 强制所有 UND 当场对账 (缺符号 = 编译期炸, 不上板才炸) ---
#   * __aeabi_uldivmod (out_write 失败路径的 64 位除法) 有意留给 bionic libc.so 的
#     LIBC_PRIVATE 导出解析 —— nm -D 实测设备 libc.so @0x8dcd8 T __aeabi_uldivmod@@LIBC_PRIVATE,
#     运行期可解析 (MTK 自家 vendor 库同样用法)。NDK 参考件则是把 compiler-rt 的
#     __udivmoddi4/__aeabi_* 以隐藏符号静态嵌进 .so, 功能等价。
#     Ubuntu armhf 的 libgcc.a 是 hard-float 多元库, 静态拉入会与 softfp 属性冲突
#     (实测 ld: "uses VFP register arguments"), 故不链 libgcc。
#   * --no-as-needed 段包住 libm/libdl: 二者无被引用符号, Ubuntu gcc 默认 --as-needed
#     会把它们的 DT_NEEDED 丢掉; 参考件 (NDK 构建) 与任务核验都要求 5 项 NEEDED。
#     镜像 /lib 下 libm.so/libdl.so 实际存在, 多两条 NEEDED 无副作用。
"$CC" -shared -nostdlib $ARCH \
    -Wl,-soname,libaudio.primary.z1.so -Wl,-z,now -Wl,--no-undefined \
    -o "$OUT" "$HERE/poc_r5g.o" \
    "$LIBDIR/libtinyalsa.so" "$LIBDIR/liblog.so" \
    -Wl,--no-as-needed "$LIBDIR/libm.so" "$LIBDIR/libdl.so" -Wl,--as-needed \
    "$LIBDIR/libc.so"

echo "[build_r5g] 产物: $OUT ($(stat -c%s "$OUT") B, sha256 $(sha256sum "$OUT" | awk '{print $1}'))"

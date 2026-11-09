#!/bin/bash
# z1_build_hal_stubs.sh — 重编两个"手写头文件"编出来的 HAL：
#   gralloc.z1.so   （16 像素对齐 + 物理连续 dma-buf 的 lima gralloc）
#   gps.default.so  （让 IGnss 服务能注册的桩 HAL）
#
# 关键教训（2026-11-08）：AOSP 的加载器是 dlsym(handle, "HMI")，
# 因为 HAL_MODULE_INFO_SYM 在 <hardware/hardware.h> 里被 #define 成 HMI。
# 手写桩头时必须把这个宏一起写进去，否则 .so 导出的是 "HAL_MODULE_INFO_SYM"，
# hw_get_module() 直接 "load: couldn't find symbol HMI" 失败 —— 而且会静默回退
# （gralloc 回退到 ashmem 老版、GPS 桩直接不加载），现象和没修一模一样。
# objcopy --redefine-sym 不管用：它不改 .dynsym，GNU hash 也是按老名字算的。
#
# 2026-11-09: gralloc.z1.so 多了 z1_heap.cpp。它负责分配**物理连续** dma-buf
# （CMA dma-heap -> mediatek-drm dumb -> lima），否则 mediatek-drm 的
# mtk_gem_prime_import_sg_table() 会因 sg_table 不连续直接 -EINVAL，OVL 永远
# 合成不了。详见 graphics/gralloc/z1_heap.cpp 头注释。
#
# 用法: z1_build_hal_stubs.sh <outdir>
#   环境变量:
#     DEVICE_LIBS=/path  设备自己的 libc++.so/libcutils.so/liblog.so 所在目录
#                        （不设时自动尝试 /tmp/z1devlibs 和老路径）
#     GRALLOC_SRC=/path  gralloc 源码目录（默认 ../graphics/gralloc）
set -eu
OUT=${1:?outdir}
NDK=${NDK:-/home/lxj/claude/6572/android-ndk-r26d}
CXX=$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/armv7a-linux-androideabi28-clang++
CC=$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/armv7a-linux-androideabi28-clang
HERE=$(cd "$(dirname "$0")" && pwd)
SHIM=$HERE/hal_stub            # hardware/hardware.h（含 HMI 宏）、log.h
GRALLOC=${GRALLOC_SRC:-$HERE/../graphics/gralloc}

# 设备 lib 目录。老默认 /tmp/sysuicrash/grallocbuild/libs 通常不存在，所以：
#  1) DEVICE_LIBS 显式指定优先；
#  2) 否则依次尝试已知位置；
#  3) 都没有就**明确报错退出**（以前会静默走到 ld 的 "-lcutils not found"）。
LIBS=${DEVICE_LIBS:-}
if [ -z "$LIBS" ]; then
    for cand in /tmp/z1devlibs /tmp/sysuicrash/grallocbuild/libs; do
        if [ -d "$cand" ]; then
            LIBS=$cand
            break
        fi
    done
fi
if [ -z "$LIBS" ] || [ ! -d "$LIBS" ]; then
    echo "ERROR: device lib dir not found." >&2
    echo "       Set DEVICE_LIBS=/path/to/dir containing libc++.so, libcutils.so, liblog.so" >&2
    echo "       (tried: \$DEVICE_LIBS, /tmp/z1devlibs, /tmp/sysuicrash/grallocbuild/libs)" >&2
    exit 1
fi
for lib in libc++.so libcutils.so liblog.so; do
    if [ ! -f "$LIBS/$lib" ]; then
        echo "ERROR: $LIBS/$lib is missing (DEVICE_LIBS=$LIBS)" >&2
        exit 1
    fi
done
mkdir -p "$OUT"

$CC -shared -fPIC -O2 -Wall -I"$SHIM" -o "$OUT/gps.default.so" "$HERE/hal_stub/gps_stub.c" \
    -Wl,-soname,gps.default.so

# -Wall 保留（新代码 z1_heap.cpp/gralloc.cpp 必须零告警）。目前 framebuffer.cpp
# 有 4 条、mapper.cpp 有 1 条既有的 unused-* 告警，与本轮改动无关。
$CXX -shared -fPIC -O2 -Wall -DLOG_TAG='"gralloc"' -DZ1_USE_LIMA=1 \
    -I"$SHIM" -I"$SHIM/hardware" -I"$GRALLOC" \
    -nostdlib++ -o "$OUT/gralloc.z1.so" \
    "$GRALLOC/gralloc.cpp" "$GRALLOC/framebuffer.cpp" "$GRALLOC/mapper.cpp" \
    "$GRALLOC/lima_allocator.cpp" "$GRALLOC/z1_heap.cpp" \
    -L"$LIBS" -lcutils -llog -lm -ldl -lc++ -Wl,-soname,gralloc.z1.so -Wl,--no-undefined

for f in gps.default.so gralloc.z1.so; do
    printf '%-18s %6s B  exports: %s\n' "$f" "$(stat -c%s "$OUT/$f")" \
        "$(arm-linux-gnueabihf-nm -D "$OUT/$f" | awk '$3=="HMI"{print "HMI OK"}' | head -1)"
done

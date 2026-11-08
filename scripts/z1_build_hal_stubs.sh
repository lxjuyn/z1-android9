#!/bin/bash
# z1_build_hal_stubs.sh — 重编两个"手写头文件"编出来的 HAL：
#   gralloc.z1.so   （16 像素对齐的 lima gralloc）
#   gps.default.so  （让 IGnss 服务能注册的桩 HAL）
#
# 关键教训（2026-11-08）：AOSP 的加载器是 dlsym(handle, "HMI")，
# 因为 HAL_MODULE_INFO_SYM 在 <hardware/hardware.h> 里被 #define 成 HMI。
# 手写桩头时必须把这个宏一起写进去，否则 .so 导出的是 "HAL_MODULE_INFO_SYM"，
# hw_get_module() 直接 "load: couldn't find symbol HMI" 失败 —— 而且会静默回退
# （gralloc 回退到 ashmem 老版、GPS 桩直接不加载），现象和没修一模一样。
# objcopy --redefine-sym 不管用：它不改 .dynsym，GNU hash 也是按老名字算的。
#
# 用法: z1_build_hal_stubs.sh <outdir>
set -eu
OUT=${1:?outdir}
NDK=/home/lxj/claude/6572/android-ndk-r26d
CXX=$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/armv7a-linux-androideabi28-clang++
CC=$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/armv7a-linux-androideabi28-clang
HERE=$(cd "$(dirname "$0")" && pwd)
SHIM=$HERE/hal_stub            # hardware/hardware.h（含 HMI 宏）、log.h
GRALLOC=${GRALLOC_SRC:-$HERE/../graphics/gralloc}
LIBS=${DEVICE_LIBS:-/tmp/sysuicrash/grallocbuild/libs}   # 设备自己的 libc++/libcutils/liblog
mkdir -p "$OUT"

$CC -shared -fPIC -O2 -I"$SHIM" -o "$OUT/gps.default.so" "$HERE/hal_stub/gps_stub.c" \
    -Wl,-soname,gps.default.so

$CXX -shared -fPIC -O2 -DLOG_TAG='"gralloc"' -DZ1_USE_LIMA=1 \
    -I"$SHIM" -I"$SHIM/hardware" -I"$GRALLOC" \
    -nostdlib++ -o "$OUT/gralloc.z1.so" \
    "$GRALLOC/gralloc.cpp" "$GRALLOC/framebuffer.cpp" "$GRALLOC/mapper.cpp" \
    "$GRALLOC/lima_allocator.cpp" \
    -L"$LIBS" -lcutils -llog -lm -ldl -lc++ -Wl,-soname,gralloc.z1.so -Wl,--no-undefined

for f in gps.default.so gralloc.z1.so; do
    printf '%-18s %6s B  exports: %s\n' "$f" "$(stat -c%s "$OUT/$f")" \
        "$(arm-linux-gnueabihf-nm -D "$OUT/$f" | awk '$3=="HMI"{print "HMI OK"}' | head -1)"
done

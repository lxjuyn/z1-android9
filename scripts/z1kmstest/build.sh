#!/bin/bash
#
# build.sh -- cross-build z1kmstest (armv7 Android 9 / API 28) with the NDK
#             clang toolchain and the pre-built static libdrm.
#
# Usage:
#   ./build.sh [OUTDIR]
#
# Environment overrides (all optional):
#   NDK        NDK root                (default: /home/lxj/claude/6572/android-ndk-r26d)
#   API        Android API level       (default: 28  -> Android 9)
#   TARGET     clang target triple     (default: armv7a-linux-androideabi${API})
#   LIBDRM_INC libdrm headers          (default: .../mesa_android/libdrm-install/include)
#   LIBDRM_A   static libdrm archive   (default: .../mesa_android/libdrm-static-build/libdrm.a)
#   CC         compiler (overrides NDK/TARGET/API entirely)
#   CFLAGS     extra compiler flags    (appended)
#   LDFLAGS    extra linker flags      (appended, before the archive)
#   STRIP      set to 0 to keep debug symbols/names
#
# Examples:
#   ./build.sh                       # -> /tmp/z1kmstest/z1kmstest
#   ./build.sh /tmp/out              # -> /tmp/out/z1kmstest
#   LIBDRM_INC=/opt/libdrm/include ./build.sh /tmp/out
#
# For a musl/glibc build (Alpine or a non-Android rootfs) the C sources build
# unchanged against the same headers:
#   CC=arm-linux-gnueabihf-gcc LDFLAGS=-static ./build.sh /tmp/z1kms-glibc
#
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
SRC=$HERE/z1kmstest.c

NDK=${NDK:-/home/lxj/claude/6572/android-ndk-r26d}
API=${API:-28}
TARGET=${TARGET:-armv7a-linux-androideabi${API}}
LIBDRM_INC=${LIBDRM_INC:-/home/lxj/claude/6572/mesa_android/libdrm-install/include}
LIBDRM_A=${LIBDRM_A:-/home/lxj/claude/6572/mesa_android/libdrm-static-build/libdrm.a}
STRIP=${STRIP:-1}

OUTDIR=${1:-/tmp/z1kmstest}
BIN=$OUTDIR/z1kmstest

[ -r "$SRC" ] || { echo "error: $SRC not found" >&2; exit 1; }
[ -r "$LIBDRM_A" ] || { echo "error: static libdrm not found: $LIBDRM_A" >&2; exit 1; }
[ -d "$LIBDRM_INC" ] || { echo "error: libdrm headers not found: $LIBDRM_INC" >&2; exit 1; }

if [ -z "${CC:-}" ]; then
	CC=$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/${TARGET}-clang
fi
[ -x "$CC" ] || { echo "error: compiler not executable: $CC" >&2; exit 1; }

# llvm-* helpers live next to the NDK clang; fall back to the host binutils if
# a non-NDK CC was requested.
NDK_BIN=$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin
if [ -x "$NDK_BIN/llvm-readelf" ]; then
	READELF=$NDK_BIN/llvm-readelf
else
	READELF=$(command -v arm-linux-gnueabihf-readelf || command -v readelf)
fi
if [ -x "$NDK_BIN/llvm-nm" ]; then
	NM=$NDK_BIN/llvm-nm
else
	NM=$(command -v arm-linux-gnueabihf-nm || command -v nm)
fi
if [ -x "$NDK_BIN/llvm-strip" ]; then
	STRIP_BIN=$NDK_BIN/llvm-strip
else
	STRIP_BIN=$(command -v arm-linux-gnueabihf-strip || command -v strip)
fi

mkdir -p "$OUTDIR"

echo "== building z1kmstest =="
echo "   CC        = $CC"
echo "   libdrm    = $LIBDRM_A (static)"
echo "   headers   = $LIBDRM_INC"
echo "   output    = $BIN"

# -I$LIBDRM_INC      -> <xf86drm.h>, <xf86drmMode.h>
# -I$LIBDRM_INC/libdrm -> <drm.h>, <drm_mode.h>, <drm_fourcc.h>  (xf86drm*.h
#                        include them as bare <drm.h> / <drm_mode.h>)
#
# -Wl,--export-dynamic puts main() and the statically linked libdrm entry
# points into .dynsym, so `nm -D` can *prove* after stripping that libdrm was
# resolved at link time and is not a runtime NEEDED library.  It costs ~9 KiB.
# shellcheck disable=SC2086
$CC -O2 -g -std=gnu11 -Wall -Wextra -Wno-unused-parameter \
	-Wl,--export-dynamic \
	-o "$BIN" "$SRC" \
	-I"$LIBDRM_INC" -I"$LIBDRM_INC/libdrm" \
	${CFLAGS:-} \
	${LDFLAGS:-} \
	"$LIBDRM_A"

if [ "$STRIP" = "1" ]; then
	$STRIP_BIN --strip-unneeded "$BIN"
fi

echo
echo "== post-build checks =="

SIZE=$(stat -c%s "$BIN")
SHA=$(sha256sum "$BIN" | cut -d' ' -f1)
FILETXT=$(file -b "$BIN")

printf 'size      : %s bytes (%s KiB)\n' "$SIZE" "$((SIZE / 1024))"
printf 'sha256    : %s\n' "$SHA"
printf 'file      : %s\n' "$FILETXT"

# --- ELF class / machine / ABI ----------------------------------------------
HDR=$($READELF -h "$BIN")
case "$HDR" in
*"ELF32"*) ;;
*) echo "CHECK FAILED: not a 32-bit ELF" >&2; exit 1 ;;
esac
case "$HDR" in
*"Machine:"*"ARM"*) ;;
*) echo "CHECK FAILED: not an ARM machine" >&2; exit 1 ;;
esac
case "$HDR" in
*"little endian"*) ;;
*) echo "CHECK FAILED: not little-endian" >&2; exit 1 ;;
esac
# readelf does not spell out "EABI5"; it is in the ARM Flags field:
#   EF_ARM_EABI_VER5 = 0x05000000, so (Flags >> 24) == 5
ARM_FLAGS=$(printf '%s\n' "$HDR" | sed -n 's/^ *Flags: *0x\([0-9a-fA-F]*\).*/0x\1/p')
if [ -n "$ARM_FLAGS" ]; then
	EABI=$(( (ARM_FLAGS >> 24) & 0xff ))
	if [ "$EABI" != "5" ]; then
		echo "CHECK FAILED: ARM EABI version is $EABI, expected 5 (EABI5)" >&2
		exit 1
	fi
	echo "elf       : ELF32 / ARM / EABI5 / LSB  ok  (ARM Flags=$ARM_FLAGS)"
else
	echo "WARNING: could not parse the ARM Flags field" >&2
fi

# --- NEEDED ------------------------------------------------------------------
echo "NEEDED    :"
NEEDED_LIST=$($READELF -d "$BIN" | sed -n 's/.*(NEEDED).*\[\(.*\)\]/\1/p')
if [ -z "$NEEDED_LIST" ]; then
	echo "            (none -- statically linked)"
else
	printf '%s\n' "$NEEDED_LIST" | sed 's/^/            /'
fi
if printf '%s\n' "$NEEDED_LIST" | grep -q 'libdrm'; then
	echo "CHECK FAILED: libdrm.so is a runtime dependency (it must be static)" >&2
	exit 1
fi
echo "            no libdrm.so dependency  ok"

# --- symbols -----------------------------------------------------------------
echo
echo "== symbol evidence (llvm-nm -D, i.e. the dynamic symbol table) =="
echo "-- main is defined in this executable:"
$NM -D "$BIN" 2>/dev/null | grep -E ' [TtWw] main$' | sed 's/^/            /' || \
	echo "            FAILED: no 'main' in .dynsym"
echo "-- the libdrm entry points this tool calls are DEFINED here ('T'), not"
echo "   undefined ('U'), which is only possible with a static libdrm:"
$NM -D "$BIN" 2>/dev/null | grep -E ' [Tt] (drmModeAtomic(AddProperty|Commit|Alloc|Free)|drmMode(Create|Destroy)PropertyBlob|drmMode(AddFB2|RmFB|GetPlane|GetPlaneResources|GetResources|GetConnector|GetEncoder|GetProperty|ObjectGetProperties|FreeProperty|FreeObjectProperties|FreePlane|FreePlaneResources|FreeConnector|FreeResources|FreeEncoder)|drmHandleEvent|drmGetVersion|drmFreeVersion|drmSetClientCap|drmIoctl)$' | sed 's/^/            /'
UNRES=$($NM -D "$BIN" 2>/dev/null | grep -cE ' U drm' || true)
printf 'undefined drm* symbols : %s\n' "$UNRES"
if [ "$UNRES" != "0" ]; then
	echo "CHECK FAILED: drm* symbols are still undefined" >&2
	exit 1
fi
printf 'total .dynsym entries  : %s\n' "$($NM -D "$BIN" 2>/dev/null | wc -l)"

echo
echo "== done: $BIN =="

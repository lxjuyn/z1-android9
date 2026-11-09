#!/usr/bin/env bash
# =============================================================================
#  z1hwc2 build harness — reproducible build + post-build verification of the
#  custom HWC2 hardware-composer HAL for the Z1
#  (MediaTek MT6572, armv7, Android 9 / LineageOS 16).
#
#  What it does
#    1. resolves the repo root from its own path (works from any cwd, follows
#       symlinks), so nothing is hard-coded to a home directory;
#    2. compiles the HAL sources for armv7a / API 28 with the NDK r26d clang
#       against the *device* libc++/liblog/libcutils and the static libdrm;
#    3. runs 5 post-build checks and exits non-zero if any of them fails;
#    4. writes a manifest.txt beside the output (source sha256, toolchain
#       version, link deps, output sha256/size).
#
#  Usage
#    ./build.sh [outdir]              build into <outdir> (default: <script>/out)
#    ./build.sh --verify-only <file>  run only the post-build checks
#    ./build.sh --help
#
#  Environment overrides
#    NDK           NDK root                       (default <repo>/android-ndk-r26d)
#    DEVICE_LIBS   device .so directory           (default /tmp/z1devlibs)
#    LIBDRM_A      static libdrm archive          (default <repo>/mesa_android/libdrm-static-build/libdrm.a)
#    LIBDRM_INC    libdrm header dir              (default <repo>/mesa_android/libdrm-install/include)
#    SRCS          space-separated C++ sources    (default z1hwc2.cpp)
#    CXX           compiler override
#    HAL_STUB      header stubs directory         (default <repo>/z1a9_gpt/scripts/hal_stub)
#    API_LEVEL     Android API level              (default 28 — must stay 28, see doc)
#    HOST_TAG      NDK prebuilt host tag          (default linux-x86_64)
#    OUT_NAME      output file name               (default hwcomposer.z1.so)
#    Z1_SYS_IMAGE  system image used for auto-extract of device libs
#    Z1_SYS_MNT    its mount point                (default /mnt/z1sys)
#
#  This script never modifies the source tree (all generated files go to
#  <outdir>) and never calls sudo: if DEVICE_LIBS is incomplete it only copies
#  from an already-mounted read-only system image, otherwise it tells you which
#  command to run.
#
#  Importable: sourcing this file only defines functions; `verify_so <file>` can
#  then be reused by other scripts.  NOTE: it does `set -eu`, which then applies
#  to the sourcing shell as well.
# =============================================================================
set -eu
export LC_ALL=C

# ---------------------------------------------------------------------------
# Expected link-time contract of the produced .so
# ---------------------------------------------------------------------------
EXPECTED_NEEDED="liblog.so libcutils.so libm.so libc++.so libdl.so libc.so"
# libs that must exist in DEVICE_LIBS for the link to target the device ABI
DEVLIBS_LINK_REQUIRED="liblog.so libcutils.so libc++.so"
# full set a device-lib directory is expected to carry
DEVLIBS_FULL="libc++.so libcutils.so liblog.so libutils.so libsync.so libhardware.so libui.so libgui.so libEGL.so libGLESv2.so libbinder.so"

# ---------------------------------------------------------------------------
# 0. self-location (no hard-coded $HOME)
# ---------------------------------------------------------------------------
_self=${BASH_SOURCE[0]:-$0}
while [ -L "$_self" ]; do
    _dir=$(cd -P -- "$(dirname -- "$_self")" && pwd)
    _self=$(readlink -- "$_self")
    case $_self in
        /*) ;;
        *) _self=$_dir/$_self ;;
    esac
done
SCRIPT_DIR=$(cd -P -- "$(dirname -- "$_self")" && pwd)
SCRIPT_NAME=$(basename -- "$_self")
SCRIPTS_DIR=$(cd -P -- "$SCRIPT_DIR/.." && pwd)
REPO_ROOT=$(cd -P -- "$SCRIPT_DIR/../../.." && pwd)

# ---------------------------------------------------------------------------
# 1. configuration (env-overridable)
# ---------------------------------------------------------------------------
NDK=${NDK:-$REPO_ROOT/android-ndk-r26d}
DEVICE_LIBS=${DEVICE_LIBS:-/tmp/z1devlibs}
LIBDRM_A=${LIBDRM_A:-$REPO_ROOT/mesa_android/libdrm-static-build/libdrm.a}
LIBDRM_INC=${LIBDRM_INC:-$REPO_ROOT/mesa_android/libdrm-install/include}
SRCS=${SRCS:-z1hwc2.cpp}
HAL_STUB=${HAL_STUB:-$SCRIPTS_DIR/hal_stub}
API_LEVEL=${API_LEVEL:-28}
HOST_TAG=${HOST_TAG:-linux-x86_64}
OUT_NAME=${OUT_NAME:-hwcomposer.z1.so}
Z1_SYS_IMAGE=${Z1_SYS_IMAGE:-$REPO_ROOT/out/z1_v35_selftest/system_v35.img}
Z1_SYS_MNT=${Z1_SYS_MNT:-/mnt/z1sys}

NDK_BIN=$NDK/toolchains/llvm/prebuilt/$HOST_TAG/bin

READELF_BIN=${READELF:-}
NM_BIN=${NM:-}
CXX_VERSION=""
CXX_TARGET=""

OUTDIR=""
OBJDIR=""
SRC_FILES=()
COMPILE_CMDS=()
LINK_CMD=""
V_SIZE=""
V_SHA=""
V_ARCH=""
V_NEEDED=""
V_HMI=""

# ---------------------------------------------------------------------------
# 2. small helpers
# ---------------------------------------------------------------------------
info() { printf '[*] %s\n' "$*"; }
warn() { printf '[!] %s\n' "$*" >&2; }
die() {
    printf '\n[x] ERROR: %s\n' "$*" >&2
    exit 1
}
sha_of() { sha256sum -- "$1" 2>/dev/null | awk '{print $1}'; }
tree_sha() { # deterministic digest over a directory tree
    (cd "$1" && find . -type f -print0 | sort -z | xargs -0 -r sha256sum) |
        sha256sum | awk '{print $1}'
}
fmt_cmd() { # shell-quoted one-line rendering of a command
    local out="" a
    for a in "$@"; do out=$out$(printf '%q' "$a")" "; done
    printf '%s' "${out% }"
}
run() { # echo then execute
    printf '  + %s\n' "$(fmt_cmd "$@")"
    "$@"
}
is_mountpoint() {
    if command -v mountpoint >/dev/null 2>&1; then
        mountpoint -q -- "$1"
        return
    fi
    awk -v m="$1" '$2 == m { f = 1 } END { exit f ? 0 : 1 }' /proc/mounts
}

usage() {
    cat <<EOF
Usage:
  $SCRIPT_NAME [outdir]              build into <outdir> (default: $SCRIPT_DIR/out)
  $SCRIPT_NAME --verify-only <file>  only run the 5 post-build checks on <file>
  $SCRIPT_NAME --help

Environment overrides:
  NDK=$NDK
  DEVICE_LIBS=$DEVICE_LIBS
  LIBDRM_A=$LIBDRM_A
  LIBDRM_INC=$LIBDRM_INC
  SRCS=$SRCS
  CXX=${CXX:-<ndk>/bin/armv7a-linux-androideabi${API_LEVEL}-clang++}
  HAL_STUB=$HAL_STUB
  API_LEVEL=$API_LEVEL   HOST_TAG=$HOST_TAG   OUT_NAME=$OUT_NAME
  Z1_SYS_IMAGE=$Z1_SYS_IMAGE   Z1_SYS_MNT=$Z1_SYS_MNT

Checks performed on the produced .so (all must pass, otherwise exit != 0):
  a) non-empty file whose 'file' description is an ELF 32-bit ARM object
  b) readelf -d NEEDED set == exactly: $EXPECTED_NEEDED
  c) 'HMI' exported as a defined data symbol (data/bss), not undefined/function
  d) no libc++_shared.so / libc++abi.so dependency
  e) sha256 + size printed (and recorded in manifest.txt)
EOF
}

resolve_tools() {
    if [ -z "$READELF_BIN" ]; then
        if command -v readelf >/dev/null 2>&1; then
            READELF_BIN=readelf
        elif [ -x "$NDK_BIN/llvm-readelf" ]; then
            READELF_BIN=$NDK_BIN/llvm-readelf
        fi
    fi
    if [ -z "$NM_BIN" ]; then
        if [ -x "$NDK_BIN/llvm-nm" ]; then
            NM_BIN=$NDK_BIN/llvm-nm
        elif command -v llvm-nm >/dev/null 2>&1; then
            NM_BIN=llvm-nm
        elif command -v nm >/dev/null 2>&1; then
            NM_BIN=nm
        fi
    fi
}

# ---------------------------------------------------------------------------
# 3. device-lib directory handling (read-only, never sudo)
# ---------------------------------------------------------------------------
devlibs_missing() { # echoes the required libs that are absent/unusable
    local l out=""
    for l in $DEVLIBS_LINK_REQUIRED; do
        [ -s "$DEVICE_LIBS/$l" ] || out="$out $l"
    done
    printf '%s' "${out# }"
}

devlibs_instructions() {
    local missing=$1
    cat >&2 <<EOF

    The link step needs the *device* libraries (libc++.so, liblog.so,
    libcutils.so) so that the .so is built against the exact ABI that is on
    the Z1.  Missing in $DEVICE_LIBS:
      $missing

    Fix it with either

      A) the helper script (it does the read-only loop mount itself, needs sudo):

           $SCRIPTS_DIR/z1_extract_devlibs.sh $DEVICE_LIBS

      B) or mount the system image read-only yourself and re-run the build:

           echo 1 | sudo -S -p '' mount -o loop,ro $Z1_SYS_IMAGE $Z1_SYS_MNT
           $SCRIPT_DIR/$SCRIPT_NAME

    This harness never calls sudo on its own.
EOF
}

ensure_devlibs() {
    local missing
    missing=$(devlibs_missing)
    if [ -z "$missing" ]; then
        info "device libs    : $DEVICE_LIBS (libc++.so/liblog.so/libcutils.so present)"
        return 0
    fi

    if [ ! -d "$DEVICE_LIBS" ]; then
        warn "DEVICE_LIBS directory does not exist: $DEVICE_LIBS"
    else
        warn "DEVICE_LIBS is incomplete: $DEVICE_LIBS (missing: $missing)"
    fi

    # Try a plain copy from an already-mounted read-only system image.
    if is_mountpoint "$Z1_SYS_MNT" && [ -d "$Z1_SYS_MNT/lib" ]; then
        warn "attempting auto-extraction from the mounted system image at $Z1_SYS_MNT"
        if [ -e "$DEVICE_LIBS" ] && [ ! -d "$DEVICE_LIBS" ]; then
            die "$DEVICE_LIBS exists but is not a directory"
        fi
        mkdir -p -- "$DEVICE_LIBS" ||
            die "cannot create $DEVICE_LIBS"
        local l n=0
        for l in $DEVLIBS_FULL; do
            if [ -s "$Z1_SYS_MNT/lib/$l" ]; then
                cp -f -- "$Z1_SYS_MNT/lib/$l" "$DEVICE_LIBS/$l" ||
                    die "failed to copy $l from $Z1_SYS_MNT/lib"
                n=$((n + 1))
            fi
        done
        info "auto-extracted $n lib(s) from $Z1_SYS_MNT/lib"
        missing=$(devlibs_missing)
        if [ -z "$missing" ]; then
            info "device libs    : $DEVICE_LIBS (auto-extracted)"
            return 0
        fi
        devlibs_instructions "$missing"
        exit 1
    fi

    devlibs_instructions "$missing"
    exit 1
}

# ---------------------------------------------------------------------------
# 4. post-build verification
# ---------------------------------------------------------------------------
# verify_so <file>
#   Runs checks (a)-(e) and returns 0 only if all pass.  Evidence is stored in
#   V_ARCH / V_SIZE / V_NEEDED / V_HMI / V_SHA for the manifest.
verify_so() {
    local so=$1
    local rc=0 idx=0
    local total=6
    local size fdesc actual expected hmi_line bad_line forbidden
    local missing extra

    V_SIZE=""
    V_SHA=""
    V_ARCH=""
    V_NEEDED=""
    V_HMI=""

    # verify_so is also used when this file is sourced, so make sure the
    # inspection tools are resolved here and not only in main().
    resolve_tools
    if [ -z "$READELF_BIN" ] || [ -z "$NM_BIN" ]; then
        printf '\n== post-build verification: %s\n' "$so"
        printf '  FAIL: readelf or nm/llvm-nm not found (set READELF=... / NM=...)\n'
        printf '  => verification FAILED\n'
        return 1
    fi

    printf '\n== post-build verification: %s\n' "$so"

    if [ ! -e "$so" ]; then
        printf '  [1/%d] %-44s FAIL (file does not exist)\n' "$total" "artifact exists / ELF 32-bit ARM"
        printf '  => verification FAILED\n'
        return 1
    fi

    # llvm-nm / llvm-readelf do not accept the "--" end-of-options marker, so
    # protect against a leading dash in the path instead.
    case $so in
        -*) so=./$so ;;
    esac

    # ---- (a) non-empty, ELF 32-bit ARM -----------------------------------
    idx=$((idx + 1))
    size=$(stat -c %s -- "$so" 2>/dev/null || printf '0')
    fdesc=$(file -b -- "$so" 2>/dev/null || printf '<file failed>')
    V_SIZE=$size
    V_ARCH=$fdesc
    printf '  [%d/%d] %-44s ' "$idx" "$total" "artifact non-empty / ELF 32-bit ARM"
    if [ "$size" -gt 0 ] && [ "${fdesc#ELF 32-bit}" != "$fdesc" ] && [ "${fdesc#*ARM}" != "$fdesc" ]; then
        printf 'PASS\n'
    else
        printf 'FAIL\n'
        rc=1
    fi
    printf '          size : %s bytes\n' "$size"
    printf '          file : %s\n' "$fdesc"

    # ---- (b) NEEDED set --------------------------------------------------
    idx=$((idx + 1))
    printf '  [%d/%d] %-44s ' "$idx" "$total" "NEEDED == expected set"
    actual=$("$READELF_BIN" -d "$so" 2>/dev/null |
        sed -n 's/.*(NEEDED).*\[\([^]]*\)\].*/\1/p' | sort -u || true)
    expected=$(printf '%s\n' $EXPECTED_NEEDED | sort -u)
    if [ -z "$actual" ]; then
        printf 'FAIL (no NEEDED found; readelf could not parse the file?)\n'
        rc=1
    elif [ "$actual" = "$expected" ]; then
        printf 'PASS\n'
        printf '%s\n' "$actual" | sed 's/^/          needed: /'
    else
        printf 'FAIL\n'
        printf '          expected: %s\n' "$(printf '%s' "$expected" | tr '\n' ' ')"
        printf '          actual  : %s\n' "$(printf '%s' "$actual" | tr '\n' ' ')"
        missing=$(comm -23 <(printf '%s\n' "$expected") <(printf '%s\n' "$actual") || true)
        extra=$(comm -13 <(printf '%s\n' "$expected") <(printf '%s\n' "$actual") || true)
        if [ -n "$missing" ]; then
            printf '%s\n' "$missing" | sed 's/^/          missing : /'
        fi
        if [ -n "$extra" ]; then
            printf '%s\n' "$extra" | sed 's/^/          unexpected: /'
        fi
        rc=1
    fi
    V_NEEDED=$actual

    # ---- (c) HMI exported as a data symbol -------------------------------
    idx=$((idx + 1))
    printf '  [%d/%d] %-44s ' "$idx" "$total" "HMI exported as data symbol"
    hmi_line=$("$NM_BIN" -D "$so" 2>/dev/null |
        awk '$NF == "HMI" && $(NF - 1) ~ /^[DGB]$/ { print; exit }' || true)
    if [ -n "$hmi_line" ]; then
        printf 'PASS\n'
        printf '          nm   : %s\n' "$hmi_line"
        V_HMI=$hmi_line
    else
        bad_line=$("$NM_BIN" -D "$so" 2>/dev/null |
            awk '$NF == "HMI" { print; exit }' || true)
        if [ -z "$bad_line" ]; then
            printf 'FAIL (symbol HMI is not exported)\n'
        else
            printf 'FAIL (HMI exported with wrong type, expected data: %s)\n' "$bad_line"
        fi
        rc=1
    fi

    # ---- (d) no shared/abi C++ runtime dependency ------------------------
    idx=$((idx + 1))
    printf '  [%d/%d] %-44s ' "$idx" "$total" "no libc++_shared / libc++abi NEEDED"
    forbidden=$(printf '%s\n' "$actual" | grep -E 'libc\+\+(abi|_shared)\.so' || true)
    if [ -z "$forbidden" ]; then
        printf 'PASS\n'
    else
        printf 'FAIL\n'
        printf '%s\n' "$forbidden" | sed 's/^/          forbidden: /'
        rc=1
    fi

    # ---- (e) sha256 + size ----------------------------------------------
    idx=$((idx + 1))
    printf '  [%d/%d] %-44s ' "$idx" "$total" "sha256 + size"
    V_SHA=$(sha_of "$so")
    if [ -n "$V_SHA" ]; then
        printf 'PASS\n'
        printf '          sha256: %s\n' "$V_SHA"
        printf '          size  : %s bytes\n' "$V_SIZE"
    else
        printf 'FAIL (sha256sum failed)\n'
        rc=1
    fi

    # ---- extra INFO (never fails the build) ------------------------------
    # The NDK links with -Wl,-z,now (BIND_NOW), so every C++ ABI symbol the
    # object leaves undefined has to be exported by a library that is present
    # on the device.  On the Z1 that is the device libc++.so, which bundles
    # libc++abi — this line records that the symbols really are available.
    local abi_undef abi_sym abi_miss abi_lib abi_n
    abi_lib=$DEVICE_LIBS/libc++.so
    if [ -r "$abi_lib" ]; then
        abi_undef=$("$NM_BIN" -D -u "$so" 2>/dev/null | awk '{print $NF}' |
            grep -E '^_*(__cxa|__gxx|_Unwind)' | grep -v '@' | sort -u || true)
        if [ -n "$abi_undef" ]; then
            abi_n=$(printf '%s\n' "$abi_undef" | wc -l)
            abi_miss=""
            while IFS= read -r abi_sym; do
                if ! "$NM_BIN" -D "$abi_lib" 2>/dev/null |
                    awk -v s="$abi_sym" '$NF == s { f = 1 } END { exit f ? 0 : 1 }'; then
                    abi_miss="$abi_miss $abi_sym"
                fi
            done <<<"$abi_undef"
            if [ -z "$abi_miss" ]; then
                printf '  [info] %-44s %s\n' "C++ ABI" \
                    "$abi_n undefined __cxa_*/__gxx_*/_Unwind_* sym(s), all exported by $(basename -- "$abi_lib")"
            else
                printf '  [info] %-44s not exported by %s:%s\n' "C++ ABI" \
                    "$(basename -- "$abi_lib")" "$abi_miss"
            fi
        fi
    fi

    # 6/6 composition policy unit test (pure header; host g++, optional)
    if command -v g++ >/dev/null 2>&1 &&
        [ -f "$SCRIPT_DIR/test/z1hwc2_policy_test.cpp" ]; then
        local ptest=$OUTDIR/z1_policy_test
        if g++ -std=c++17 -O1 -Wall -Wextra -o "$ptest" \
            "$SCRIPT_DIR/test/z1hwc2_policy_test.cpp" \
            >"$OUTDIR/policy_test.build.log" 2>&1 &&
            "$ptest" >"$OUTDIR/policy_test.log" 2>&1; then
            printf '  [6/%d] %-44s PASS\n' "$total" "composition policy unit test"
            printf '          cases: %s\n' \
                "$(grep -c '^  PASS' "$OUTDIR/policy_test.log")"
        else
            printf '  [6/%d] %-44s FAIL\n' "$total" "composition policy unit test"
            printf '          see %s\n' "$OUTDIR/policy_test.log"
            rc=1
        fi
    else
        printf '  [6/%d] %-44s SKIP (no host g++)\n' "$total" \
            "composition policy unit test"
    fi

    if [ "$rc" -eq 0 ]; then
        printf '  => verification OK\n'
    else
        printf '  => verification FAILED\n'
    fi
    return "$rc"
}

# ---------------------------------------------------------------------------
# 5. manifest
# ---------------------------------------------------------------------------
write_manifest() {
    local so=$1
    local manifest=$OUTDIR/manifest.txt
    local f h i=0

    {
        printf '# Z1 HWC2 build manifest\n'
        printf '# generated by %s\n' "$SCRIPT_NAME"
        printf 'generated_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        printf 'host=%s\n' "$(uname -srm)"
        printf 'repo_root=%s\n' "$REPO_ROOT"
        printf 'outdir=%s\n' "$OUTDIR"
        printf '\n[output]\n'
        printf 'path=%s\n' "$so"
        printf 'size=%s\n' "$V_SIZE"
        printf 'sha256=%s\n' "$V_SHA"
        printf 'file=%s\n' "$V_ARCH"
        printf 'soname=%s\n' "$OUT_NAME"
        printf 'needed=%s\n' "$(printf '%s' "$V_NEEDED" | tr '\n' ' ' | sed 's/ $//')"
        printf 'hmi_symbol=%s\n' "$V_HMI"
        printf '\n[toolchain]\n'
        printf 'ndk=%s\n' "$NDK"
        printf 'cxx=%s\n' "$CXX"
        printf 'version=%s\n' "$CXX_VERSION"
        printf 'target=%s\n' "$CXX_TARGET"
        printf 'api_level=%s\n' "$API_LEVEL"
        printf 'host_tag=%s\n' "$HOST_TAG"
        printf '\n[link]\n'
        printf 'libdrm_static=%s\n' "$LIBDRM_A"
        printf 'libdrm_static_sha256=%s\n' "$(sha_of "$LIBDRM_A")"
        printf 'libdrm_headers=%s\n' "$LIBDRM_INC"
        printf 'device_libs=%s\n' "$DEVICE_LIBS"
        printf 'link_libs=%s\n' "-llog -lcutils -lm"
        printf 'link_cmd=%s\n' "$LINK_CMD"
        printf '\n[sources]\n'
        for f in "${SRC_FILES[@]}"; do
            printf 'path=%s sha256=%s bytes=%s\n' "$f" "$(sha_of "$f")" "$(stat -c %s -- "$f")"
        done
        printf '\n[headers]\n'
        for h in "$SCRIPT_DIR/include/hardware/hardware.h" \
            "$SCRIPT_DIR/include/hardware/hwcomposer2.h" \
            "$SCRIPT_DIR/include/hardware/hwcomposer_defs.h" \
            "$LIBDRM_INC/xf86drm.h" "$LIBDRM_INC/xf86drmMode.h" \
            "$LIBDRM_INC/libdrm/drm_fourcc.h"; do
            if [ -f "$h" ]; then
                printf 'path=%s sha256=%s\n' "$h" "$(sha_of "$h")"
            fi
        done
        printf 'hal_stub_dir=%s tree_sha256=%s\n' "$HAL_STUB" "$(tree_sha "$HAL_STUB")"
        printf '\n[device_libs_sha256]\n'
        for h in $DEVLIBS_LINK_REQUIRED; do
            printf '%s sha256=%s bytes=%s\n' "$h" "$(sha_of "$DEVICE_LIBS/$h")" \
                "$(stat -c %s -- "$DEVICE_LIBS/$h" 2>/dev/null || printf '0')"
        done
        printf '\n[compile]\n'
        for i in "${!COMPILE_CMDS[@]}"; do
            printf 'cmd_%d=%s\n' "$((i + 1))" "${COMPILE_CMDS[$i]}"
        done
        printf '\n[verification]\n'
        printf 'arch_elf32_arm=pass\n'
        printf 'needed_exact_set=pass\n'
        printf 'hmi_exported_data_symbol=pass\n'
        printf 'no_libcxx_shared_or_abi=pass\n'
        printf 'sha256_size_reported=pass\n'
        printf 'result=pass\n'
    } >"$manifest" || die "cannot write manifest: $manifest"

    info "manifest       : $manifest"
}

# ---------------------------------------------------------------------------
# 6. build
# ---------------------------------------------------------------------------
build() {
    local outdir=$1
    local f o i=0 s

    printf '============================================================\n'
    printf ' Z1 HWC2 build harness\n'
    printf '============================================================\n'
    info "repo root      : $REPO_ROOT"
    info "script         : $SCRIPT_DIR/$SCRIPT_NAME"
    info "ndk            : $NDK"
    info "device libs    : $DEVICE_LIBS"
    info "libdrm archive : $LIBDRM_A"
    info "libdrm headers : $LIBDRM_INC"
    info "hal stubs      : $HAL_STUB"
    info "sources        : $SRCS"
    info "api level      : $API_LEVEL"

    if [ ! -d "$REPO_ROOT/z1a9_gpt" ]; then
        die "repo root looks wrong ($REPO_ROOT); the script must live in <repo>/z1a9_gpt/scripts/z1hwc2/"
    fi

    resolve_tools
    if [ -z "$READELF_BIN" ]; then
        die "readelf not found (install binutils or use the NDK's llvm-readelf via READELF=...)"
    fi
    if [ -z "$NM_BIN" ]; then
        die "nm/llvm-nm not found (install binutils or use the NDK's llvm-nm via NM=...)"
    fi

    # --- compiler --------------------------------------------------------
    if [ -n "${CXX:-}" ]; then
        :
    elif [ -x "$NDK_BIN/armv7a-linux-androideabi${API_LEVEL}-clang++" ]; then
        CXX=$NDK_BIN/armv7a-linux-androideabi${API_LEVEL}-clang++
    else
        die "C++ compiler not found: $NDK_BIN/armv7a-linux-androideabi${API_LEVEL}-clang++
    set NDK=/path/to/android-ndk-r26d (or CXX=/path/to/clang++)"
    fi
    if [ ! -x "$CXX" ]; then
        die "C++ compiler is not executable: $CXX"
    fi
    CXX_VERSION=$("$CXX" --version 2>/dev/null | head -n1 || true)
    CXX_TARGET=$("$CXX" --version 2>/dev/null | sed -n 's/^Target: //p' | head -n1 || true)
    if [ -z "$CXX_VERSION" ]; then
        die "cannot run the compiler: $CXX --version"
    fi
    info "compiler       : $CXX"
    info "compiler ver   : $CXX_VERSION"
    info "compiler target: $CXX_TARGET"
    info "readelf/nm     : $READELF_BIN / $NM_BIN"

    # --- required inputs -------------------------------------------------
    if [ ! -d "$HAL_STUB" ]; then
        die "header stubs not found: $HAL_STUB (set HAL_STUB=...)"
    fi
    if [ ! -f "$SCRIPT_DIR/include/hardware/hwcomposer2.h" ]; then
        die "HAL headers missing: $SCRIPT_DIR/include/hardware/hwcomposer2.h"
    fi
    if [ ! -d "$LIBDRM_INC" ]; then
        die "libdrm header dir not found: $LIBDRM_INC (set LIBDRM_INC=...)"
    fi
    if [ ! -f "$LIBDRM_INC/libdrm/drm_fourcc.h" ]; then
        die "libdrm headers incomplete: $LIBDRM_INC/libdrm/drm_fourcc.h missing"
    fi
    if [ ! -d "$LIBDRM_A" ] && [ ! -f "$LIBDRM_A" ]; then
        die "static libdrm not found: $LIBDRM_A (set LIBDRM_A=...)"
    fi
    ensure_devlibs

    # --- resolve sources -------------------------------------------------
    for s in $SRCS; do
        case $s in
            /*) f=$s ;;
            *)
                if [ -f "$SCRIPT_DIR/$s" ]; then
                    f=$SCRIPT_DIR/$s
                elif [ -f "$s" ]; then
                    f=$s
                else
                    die "source not found: $s (looked in $SCRIPT_DIR and $PWD)"
                fi
                ;;
        esac
        if [ ! -f "$f" ]; then
            die "source not found: $f"
        fi
        SRC_FILES+=("$(cd -P -- "$(dirname -- "$f")" && pwd)/$(basename -- "$f")")
    done
    if [ "${#SRC_FILES[@]}" -eq 0 ]; then
        die "no source files to build (SRCS='$SRCS')"
    fi

    # --- output dir ------------------------------------------------------
    if [ -e "$outdir" ] && [ ! -d "$outdir" ]; then
        die "output path exists and is not a directory: $outdir"
    fi
    mkdir -p -- "$outdir" || die "cannot create output directory: $outdir"
    OUTDIR=$(cd -P -- "$outdir" && pwd)
    OBJDIR=$OUTDIR/obj
    mkdir -p -- "$OBJDIR" || die "cannot create object directory: $OBJDIR"
    rm -f -- "$OUTDIR/$OUT_NAME"
    info "outdir         : $OUTDIR"

    # --- compile ---------------------------------------------------------
    local -a cflags include_dirs
    cflags=(-c -O2 -fPIC -std=gnu++17 -Wall -Wextra)
    include_dirs=(-I "$SCRIPT_DIR/include" -I "$HAL_STUB" -I "$LIBDRM_INC" -I "$LIBDRM_INC/libdrm")

    printf '\n[1/3] compile\n'
    local -a objs=()
    for f in "${SRC_FILES[@]}"; do
        i=$((i + 1))
        o=$OBJDIR/$(printf '%02d' "$i")_$(basename -- "${f%.*}").o
        if ! run "$CXX" "${cflags[@]}" "${include_dirs[@]}" -o "$o" "$f"; then
            die "compile failed: $f"
        fi
        if [ ! -s "$o" ]; then
            die "compiler produced no object file: $o"
        fi
        COMPILE_CMDS+=("$(fmt_cmd "$CXX" "${cflags[@]}" "${include_dirs[@]}" -o "$o" "$f")")
        objs+=("$o")
        info "object         : $o ($(stat -c %s -- "$o") bytes)"
    done

    # --- link ------------------------------------------------------------
    printf '\n[2/3] link\n'
    local -a link_argv
    link_argv=("$CXX" -shared -o "$OUTDIR/$OUT_NAME" "${objs[@]}" "$LIBDRM_A"
        -L"$DEVICE_LIBS" -llog -lcutils -lm -Wl,-soname,"$OUT_NAME")
    LINK_CMD=$(fmt_cmd "${link_argv[@]}")
    printf '  + %s\n' "$LINK_CMD"
    if ! "${link_argv[@]}"; then
        die "link failed: $OUTDIR/$OUT_NAME"
    fi
    info "output         : $OUTDIR/$OUT_NAME ($(stat -c %s -- "$OUTDIR/$OUT_NAME") bytes)"

    # --- verify ----------------------------------------------------------
    printf '\n[3/3] verify\n'
    if ! verify_so "$OUTDIR/$OUT_NAME"; then
        printf '\n[x] BUILD FAILED: post-build verification failed for %s\n' \
            "$OUTDIR/$OUT_NAME" >&2
        exit 1
    fi

    # --- manifest + summary ----------------------------------------------
    write_manifest "$OUTDIR/$OUT_NAME"
    printf '\n============================================================\n'
    printf ' BUILD OK\n'
    printf '============================================================\n'
    printf ' output      : %s\n' "$OUTDIR/$OUT_NAME"
    printf ' size        : %s bytes\n' "$V_SIZE"
    printf ' sha256      : %s\n' "$V_SHA"
    printf ' needed      : %s\n' "$(printf '%s' "$V_NEEDED" | tr '\n' ' ' | sed 's/ $//')"
    printf ' HMI symbol  : %s\n' "$V_HMI"
    printf ' manifest    : %s/manifest.txt\n' "$OUTDIR"
    printf '============================================================\n'
}

# ---------------------------------------------------------------------------
# 7. entry point
# ---------------------------------------------------------------------------
main() {
    case ${1:-} in
        --verify-only)
            if [ "$#" -ne 2 ]; then
                usage >&2
                exit 2
            fi
            resolve_tools
            if [ -z "$READELF_BIN" ]; then
                die "readelf not found (set READELF=... or install binutils)"
            fi
            if [ -z "$NM_BIN" ]; then
                die "nm/llvm-nm not found (set NM=... or install binutils)"
            fi
            if verify_so "$2"; then
                exit 0
            fi
            exit 1
            ;;
        -h | --help)
            usage
            exit 0
            ;;
        -*)
            usage >&2
            exit 2
            ;;
    esac

    if [ "$#" -gt 1 ]; then
        usage >&2
        exit 2
    fi

    build "${1:-$SCRIPT_DIR/out}"
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    main "$@"
fi

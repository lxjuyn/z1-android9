#!/bin/bash
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../../.." && pwd)
ndk=${ANDROID_NDK_HOME:-"$root/android-ndk-r26d"}
toolchain="$ndk/toolchains/llvm/prebuilt/linux-x86_64/bin"
cxx="$toolchain/armv7a-linux-androideabi28-clang++"
if [ "$#" -ne 1 ]; then
    echo "usage: $0 NEW_OUTPUT_DIRECTORY" >&2
    exit 2
fi
output=$1
mkdir "$output"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
common=(-std=c++17 -Os -Wall -Wextra -Werror)

"${CXX:-c++}" "${common[@]}" -o "$work/host" "$here/test_charge_ui.cpp"
"$cxx" "${common[@]}" -static -Wl,--gc-sections -o "$work/arm" "$here/test_charge_ui.cpp"
"$cxx" "${common[@]}" -static -Wl,--gc-sections -o "$work/ui" "$here/z1_charge_ui.cpp"
"$toolchain/llvm-strip" "$work/ui"
"$work/host" "$work/fixture" | tee "$output/TESTS.txt"
qemu-arm "$work/arm" "$work/fixture" | tee -a "$output/TESTS.txt"
install -m 0755 "$work/ui" "$output/z1_charge_ui"
(cd "$output" && sha256sum z1_charge_ui >SHA256SUMS)

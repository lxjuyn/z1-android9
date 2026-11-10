#!/usr/bin/env bash
# Freshly compile the current production source and synthetic fixtures every run.
set -euo pipefail
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source_file="$here/../libbt-vendor-mtk.c"
probe=0
if [[ ${1:-} == --probe-cleanup-failure && $# == 1 ]]; then probe=1
elif [[ $# != 0 ]]; then echo "usage: $0 [--probe-cleanup-failure]" >&2; exit 2; fi
work=$(mktemp -d "${TMPDIR:-/tmp}/z1-bt-regression.XXXXXXXX")
trap 'rm -rf -- "$work"' EXIT
cc=${CC:-cc}
flags=(-std=c11 -D_POSIX_C_SOURCE=200809L -Wall -Wextra -Werror -g -O1)
if [[ ${SANITIZE:-0} == 1 ]]; then
  flags+=(-fsanitize=address,undefined -fno-omit-frame-pointer)
fi
mkdir -p "$work/good" "$work/missing-enable" "$work/missing-disable" "$work/invalid"
# Stable snapshot + before/after hash: never execute an archived production .so.
cp -- "$source_file" "$work/libbt-vendor-mtk.c"
source_hash=$(sha256sum "$work/libbt-vendor-mtk.c" | cut -d ' ' -f1)
printf 'SOURCE sha256=%s\n' "$source_hash"
"$cc" "${flags[@]}" -fPIC -shared -I"$here/include" -I"$here/shim_host" \
  "$work/libbt-vendor-mtk.c" "$here/loader_fixture.c" -ldl \
  -Wl,--no-undefined -Wl,--wrap=dlopen -Wl,--wrap=dlclose \
  -o "$work/libbt-vendor-host.so"
for variant in good missing-enable missing-disable; do
  define=()
  [[ $variant != missing-enable ]] || define=(-DMOCK_MISSING_ENABLE)
  [[ $variant != missing-disable ]] || define=(-DMOCK_MISSING_DISABLE)
  "$cc" "${flags[@]}" "${define[@]}" -fPIC -shared "$here/mock_drv.c" \
    -Wl,--no-undefined -o "$work/$variant/libbluetoothdrv.so"
done
printf 'Deliberately invalid ELF test fixture\n' > "$work/invalid/libbluetoothdrv.so"
"$cc" "${flags[@]}" -I"$here/include" "$here/test_vendor.c" -ldl -o "$work/test_vendor"
tests=(lifecycle fd-zero close-retry missing-enable missing-disable dlopen-missing dlopen-invalid)
[[ $probe == 0 ]] || tests+=(cleanup-disable-failure)
failed=0
for test in "${tests[@]}"; do
  fixture="$work/good/libbluetoothdrv.so"
  case "$test" in
    missing-enable|missing-disable) fixture="$work/$test/libbluetoothdrv.so" ;;
    dlopen-missing) fixture="$work/not-created/libbluetoothdrv.so" ;;
    dlopen-invalid) fixture="$work/invalid/libbluetoothdrv.so" ;;
  esac
  # Absolute path redirection prevents fallback to a real system/vendor library.
  if ! env -u LD_PRELOAD -u LD_LIBRARY_PATH Z1_TEST_DRIVER="$fixture" \
      timeout 20 "$work/test_vendor" "$work/libbt-vendor-host.so" "$test" \
      "$work/good/libbluetoothdrv.so"; then failed=$((failed + 1)); fi
done
current_hash=$(sha256sum "$source_file" | cut -d ' ' -f1)
if [[ $source_hash != "$current_hash" ]]; then
  echo 'ERROR: production source changed during test run' >&2; exit 2
fi
printf 'RESULT cases=%d failed=%d source=%s\n' "${#tests[@]}" "$failed" "$source_hash"
[[ $failed == 0 ]]

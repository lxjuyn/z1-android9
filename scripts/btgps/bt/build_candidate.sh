#!/usr/bin/env bash
# Offline-only ARM API28 build. Never deploys, flashes, or touches a device.
set -euo pipefail
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo=$(cd -- "$script_dir/../../.." && pwd)
workspace=$(dirname -- "$repo")
ndk=${ANDROID_NDK_HOME:-"$workspace/android-ndk-r26d"}
archive_root=${BT_ARCHIVE_ROOT:-"$workspace/bridge_backups/products"}
if [[ $# != 0 ]]; then
  echo 'usage: ANDROID_NDK_HOME=/path/to/android-ndk-r26d BT_ARCHIVE_ROOT=/existing/archive ./build_candidate.sh' >&2
  exit 2
fi
[[ -d $archive_root ]] || { echo "Archive root missing: $archive_root" >&2; exit 2; }
grep -qx 'Pkg.Revision = 26.3.11579264' "$ndk/source.properties" || {
  echo 'This candidate requires Android NDK r26d (26.3.11579264)' >&2; exit 2;
}
toolchain="$ndk/toolchains/llvm/prebuilt/linux-x86_64/bin"
cc="$toolchain/armv7a-linux-androideabi28-clang"
readelf="$toolchain/llvm-readelf"
[[ -x $cc && -x $readelf ]]
source_file="$repo/bluetooth/libbt-vendor-mtk/libbt-vendor-mtk.c"
tests="$repo/bluetooth/libbt-vendor-mtk/tests"
work=$(mktemp -d "${TMPDIR:-/tmp}/z1-bt-arm28.XXXXXXXX")
trap 'rm -rf -- "$work"' EXIT
mkdir -p "$work/source/tests"
cp -- "$source_file" "$work/source/libbt-vendor-mtk.c"
cp -a -- "$tests/." "$work/source/tests/"
cp -- "$script_dir/build_candidate.sh" "$work/source/build_candidate.sh"
source_hash=$(sha256sum "$work/source/libbt-vendor-mtk.c" | cut -d ' ' -f1)
# Run the retained host tests on this build's source snapshot before compiling ARM.
"$work/source/tests/run_host.sh" > "$work/host_tests.log" 2>&1
"$cc" --version > "$work/compiler.txt"
cp -- "$ndk/source.properties" "$work/ndk-source.properties"
command=("$cc" -std=c11 -Wall -Wextra -Werror -Os -fPIC -shared
  -I"$work/source/tests/include" -I"$work/source/tests/shim_android"
  -include "$work/source/tests/arm_abi_checks.h"
  "$work/source/libbt-vendor-mtk.c" -Wl,--no-undefined
  -Wl,-soname,libbt-vendor.so -Wl,--build-id=sha1 -Wl,-z,relro,-z,now
  -llog -ldl -o "$work/libbt-vendor.so")
printf '%q ' "${command[@]}" > "$work/build-command.txt"
printf '\n' >> "$work/build-command.txt"
"${command[@]}" > "$work/build.log" 2>&1
# Archive the newly generated product immediately, before further verification.
archive=$(mktemp -d "$archive_root/products_btvendor_independent_$(date +%Y%m%d_%H%M%S)_XXXXXXXX")
cp -- "$work/libbt-vendor.so" "$archive/libbt-vendor.so"
printf 'ARCHIVE=%s\n' "$archive"
cp -a -- "$work/source" "$archive/source"
cp -- "$work/host_tests.log" "$work/compiler.txt" "$work/ndk-source.properties" \
  "$work/build-command.txt" "$work/build.log" "$archive/"
"$readelf" --file-header --dynamic --dyn-syms --notes --version-info --arch-specific \
  "$archive/libbt-vendor.so" > "$archive/readelf.txt"
python3 - "$archive" "$repo" "$source_file" "$source_hash" <<'PYCHECK'
import datetime, hashlib, json, pathlib, re, subprocess, sys
archive, repo, live, source_hash = sys.argv[1:]
a = pathlib.Path(archive)
text = (a / 'readelf.txt').read_text()
needed = re.findall(r'\(NEEDED\).*?\[([^\]]+)\]', text)
assert 'Class:                             ELF32' in text, 'not ELF32'
assert re.search(r'Machine:\s+ARM\b', text), 'not ARM'
assert re.search(r'\(SONAME\).*\[libbt-vendor\.so\]', text), 'bad SONAME'
assert set(needed) == {'libc.so', 'libdl.so', 'liblog.so'}, needed
assert 'GLIBC_' not in text and 'ld-linux' not in text, 'host ABI contamination'
assert not re.search(r'\((RPATH|RUNPATH|TEXTREL)\)', text), 'unsafe dynamic tag'
assert re.search(r'\b16\s+OBJECT\s+GLOBAL\s+DEFAULT\s+\d+\s+BLUETOOTH_VENDOR_LIB_INTERFACE\b', text), 'missing/wrong-size interface'
assert not re.search(r'\b(mock_|test_loader_|__wrap_)', text), 'test hook in ARM product'
assert re.search(r'Android\s+0x[0-9a-f]+\s+NT_ANDROID_TYPE_IDENT', text), 'Android note missing'
assert re.search(r'description data: 1c 00 00 00 72 32 36 64 00', text), 'not API28 / r26d'
assert hashlib.sha256(pathlib.Path(live).read_bytes()).hexdigest() == source_hash, 'production changed during build'
try:
    head = subprocess.check_output(['git', '-C', repo, 'rev-parse', 'HEAD'], text=True).strip()
except subprocess.CalledProcessError:
    head = 'unknown'
metadata = {'built_at': datetime.datetime.now().astimezone().isoformat(),
            'source_sha256': source_hash, 'source_path': live, 'repository_head': head,
            'candidate_sha256': hashlib.sha256((a/'libbt-vendor.so').read_bytes()).hexdigest(),
            'ndk': 'r26d / 26.3.11579264', 'target': 'armv7a-linux-androideabi28',
            'needed': needed, 'no_undefined': True, 'abi_checks': 'passed',
            'host_regressions': '7 cases passed', 'board_validated': False,
            'known_limitations': ['cleanup failure retention is tested; persistent factory failure or unloading the adapter may still prevent recovery',
                                  'NVRAM and actual target library mappings unverified',
                                  'no actual controller, HCI data transport, pairing, or audio validation']}
(a/'build_metadata.json').write_text(json.dumps(metadata, indent=2)+'\n')
(a/'SOURCE_SHA256SUMS').write_text(''.join(
    hashlib.sha256(p.read_bytes()).hexdigest()+'  '+str(p.relative_to(a))+'\n'
    for p in sorted((a/'source').rglob('*')) if p.is_file()))
(a/'SHA256SUMS').write_text(''.join(
    hashlib.sha256(p.read_bytes()).hexdigest()+'  '+str(p.relative_to(a))+'\n'
    for p in sorted(a.rglob('*')) if p.is_file() and p.name != 'SHA256SUMS'))
print(json.dumps(metadata, indent=2))
PYCHECK

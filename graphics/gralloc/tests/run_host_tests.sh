#!/bin/bash
set -euo pipefail
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
test_dir=$(mktemp -d /tmp/z1-gralloc-tests.XXXXXX)
trap 'rm -rf "$test_dir"' EXIT
"${CXX:-g++}" -std=c++11 -Wall -Wextra -Werror -fsanitize=address,undefined \
  -fno-omit-frame-pointer -g "$source_dir/pixel_copy_test.cpp" -o "$test_dir/pixel_copy"
"$test_dir/pixel_copy"
# Only unavailable Android framework declarations are stubbed. The production
# private_handle_t header supplies constructor, fields, validation and ABI.
mkdir -p "$test_dir/include/hardware" "$test_dir/include/cutils"
cat > "$test_dir/include/hardware/gralloc.h" <<'STUB'
struct gralloc_module_t { int placeholder; };
typedef const struct native_handle* buffer_handle_t;
STUB
cat > "$test_dir/include/cutils/native_handle.h" <<'STUB'
struct native_handle { int version; int numFds; int numInts; };
typedef native_handle native_handle_t;
STUB
"${CXX:-g++}" -std=c++11 -Wall -Wextra -Werror -I"$test_dir/include" \
  "$source_dir/handle_wire_test.cpp" -o "$test_dir/handle_wire"
"$test_dir/handle_wire"

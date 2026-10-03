#!/bin/bash
set -euo pipefail
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
test_dir=$(mktemp -d /tmp/z1-health-tests.XXXXXX)
trap 'rm -rf "$test_dir"' EXIT
"${CXX:-g++}" -std=c++11 -Wall -Wextra -Werror -fsanitize=address,undefined \
    -fno-omit-frame-pointer "$source_dir/capacity_test.cpp" -o "$test_dir/test"
"$test_dir/test" "$test_dir/capacity"

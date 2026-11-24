#!/bin/bash
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
HAL="$HERE/../../../../hal_stub"
ANDROID_HAL="$HERE/../../../../r5g_audio/inc"
CC=${CC:-arm-linux-gnueabihf-gcc}
QEMU=${QEMU:-qemu-arm}

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT HUP INT TERM

"$CC" -shared -fPIC -O2 -Wall -Wextra -Werror \
    -I"$HAL" -I"$ANDROID_HAL" \
    -DZ1_STOCK_GPS_HAL_PATH="\"$work/gps.z1stock.so\"" \
    -Wl,-soname,gps.default.so \
    -o "$work/gps.default.so" \
    "$HERE/../gps_adapter.c" "$HERE/mock_log.c" -ldl
"$CC" -shared -fPIC -O2 -Wall -Wextra -Werror \
    -I"$HAL" -Wl,-soname,gps.z1stock.so \
    -o "$work/gps.z1stock.so" "$HERE/mock_stock.c"
"$CC" -fPIE -pie -O2 -Wall -Wextra -Werror \
    -I"$HAL" -I"$ANDROID_HAL" \
    -DZ1_ADAPTER_PATH="\"$work/gps.default.so\"" \
    -DZ1_STOCK_HAL_PATH="\"$work/gps.z1stock.so\"" \
    -o "$work/test" "$HERE/test_adapter.c" -ldl -lpthread

"$QEMU" -L /usr/arm-linux-gnueabihf "$work/test"

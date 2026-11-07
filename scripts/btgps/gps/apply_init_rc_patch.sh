#!/bin/sh
# Apply the service rename to an unpacked ramdisk tree (host side).
# Usage: ./apply_init_rc_patch.sh /path/to/unpacked/ramdisk
set -e
RD=${1:?usage: $0 <ramdisk-tree>}
f=$RD/init.z1.network.rc
[ -f "$f" ] || { echo "no $f" >&2; exit 1; }
grep -q '^service z1_mnld /vendor/bin/mnld' "$f"
sed -i 's|^service z1_mnld /vendor/bin/mnld$|service mnld /vendor/bin/mnld|' "$f"
grep -n '^service mnld ' "$f"
echo "OK: repack the ramdisk (and update z1_gps_prep.sh 'start mnld')"

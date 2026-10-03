# Z1 Android 9 (LineageOS 16.0)

Single-device Android 9 project for the Jiabo Z1 / MT6572. The repository tracks the Z1 device tree, stock-kernel compatibility patches, build and verification helpers, and a pinned Android source manifest. Large upstream Android sources are synced by `repo`; generated build products and images are kept out of Git.

## Current candidate: S1I

S1I fixes the observed AlarmManager `CLOCK_BOOTTIME` timerfd abort while retaining the original Z1 3.4.67 kernel. The source change is isolated in its own commit and has a source-level host fixture. Static image checks passed; S1I has not yet been runtime boot-tested. Release notes and SHA-256 values are in `release/S1I-20261004/`.

## Source and builds

`manifests/lineage16-pinned.xml` pins the exact upstream project revisions used by the workspace before device patch application. `stock_patches/manifest.json` records each patch's base and result SHA-256. Apply with `python3 scripts/apply_stock_patches.py --source /path/to/los16`.

Set `LOS_ROOT` and `HOST_TOOLS`, then run `scripts/build_z1.sh systemimage`. The stock profile keeps the original Z1 3.4.67 kernel.

## Changes and releases

Make one focused fix per commit. Do not commit userdata, raw serial logs, build outputs, images, credentials, access tokens, SSH keys, or personal environment files. Flashable images are published as private GitHub Release assets with checksums; only system/boot partitions are packaged, never userdata.

# Z1 Android 9 (LineageOS 16.0)

Single-device Android 9 project for the Jiabo Z1 / MT6572. The repository tracks the Z1 device tree, stock-kernel compatibility patches, build and verification helpers, and a pinned Android source manifest. Large upstream Android sources are synced by `repo`; generated build products and images are kept out of Git.

## Current candidate: S1J diagnostic baseline

S1J retains the original Z1 3.4.67 kernel and the `CLOCK_BOOTTIME` timerfd fallback. The stock product omits remote HIDL and vendor Binder service processes that require Binder protocols or device nodes the factory kernel does not provide, while retaining client-loaded passthrough HALs. Static init, ELF dependency, classpath and ext4 checks pass. The image has not been boot-tested on the Z1. It uses permissive policy 26 with unknown rules allowed, so treat it as a bring-up image, not a daily-use build.

The current baseline still uses SwiftShader and a silent audio stub. Wi-Fi and Bluetooth have only low-level WMT firmware startup staged. Display, touch and key mappings need another hardware verification pass. Do not describe those features as complete until they are tested on the device.

## Source and builds

A clean checkout uses this repository as the Repo manifest and syncs the pinned Android/LineageOS projects plus the Z1 device tree:

```sh
repo init -u https://github.com/lxjuyn/z1-android9.git -b main -m default.xml
repo sync -j4
python3 device/jiabo/z1/scripts/apply_stock_patches.py --source "$PWD"
```

Use `scripts/sync_sources.sh /path/to/workspace` to run those steps.

`manifests/lineage16-pinned.xml` pins the exact upstream project revisions used by the workspace before device patch application. `stock_patches/manifest.json` records each patch's base and result SHA-256. Apply with `python3 scripts/apply_stock_patches.py --source /path/to/los16`.

Set `LOS_ROOT` and `HOST_TOOLS`, then run `scripts/build_z1.sh systemimage`. The stock profile keeps the original Z1 3.4.67 kernel.

## Changes and releases

Make one focused fix per commit. Do not commit userdata, raw serial logs, build outputs, images, credentials, access tokens, SSH keys, or personal environment files. Flashable images are published as private GitHub Release assets with checksums; only system/boot partitions are packaged, never userdata.

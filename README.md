# Z1 Android 9 (LineageOS 16.0)

Single-device Android 9 project for the Jiabo Z1 / MT6572. The repository tracks the Z1 device tree, stock-kernel compatibility patches, build and verification helpers, and a pinned Android source manifest. Large upstream Android sources are synced by `repo`; generated build products and images are kept out of Git.

## Source snapshot

`manifests/lineage16-pinned.xml` pins the exact upstream project revisions used by the current workspace before the Z1 patch series is applied. `stock_patches/manifest.json` records per-file base and result hashes. Run `python3 scripts/apply_stock_patches.py --source /path/to/los16` after syncing. The initial import represents the S1H state; the S1I AlarmManager repair will be a separate commit.

## Build

The build wrapper expects an existing LineageOS 16.0 source checkout and host compatibility tools. Set `LOS_ROOT` and `HOST_TOOLS` for your machine, then run `scripts/build_z1.sh systemimage` or the required target. The selected stock build uses the original Z1 3.4.67 kernel profile. This repository does not include userdata or build caches.

## Images

Flashable outputs are published as private GitHub Release assets, not Git objects. Releases include checksums and validation notes. The current S1I system image is a candidate and has not yet been runtime boot-verified.

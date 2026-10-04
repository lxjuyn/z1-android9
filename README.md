# Z1 Android 9 (LineageOS 16.0)

Single-device Android 9 project for the Jiabo Z1 / MT6572. The repository tracks the Z1 device tree, stock-kernel compatibility patches, build and verification helpers, and a pinned Android source manifest. Large upstream Android sources are synced by `repo`; generated products and images stay out of Git.

## Current work: S1M display-console follow-up

S1M is a boot-only update built from the S1L system image. It keeps the exact Z1 3.4.67 kernel and adds a ramdisk script that detaches only the framebuffer console after Android reaches `post-fs-data`; serial kernel logging remains enabled. The boot image fits the 6 MiB boot partition and passes static structure and ramdisk checks. Host tests pass, but the display fix has not yet been verified on the device. The S1L Release remains the previous two-partition candidate; it does not contain this S1M boot change.

## S1L base system status

S1L uses the original Z1 3.4.67 kernel. Its `boot.img` and raw 943,718,400-byte ext4 `system.img` passed image, filesystem, init and ELF dependency checks. It has not been boot-tested on the device after the current Bluetooth and Wi-Fi integration. The SELinux policy is permissive version 26 with unknown rules allowed, so this is a controlled bring-up candidate, not a daily-use build.

An earlier run with this factory kernel repeatedly aborted `system_server`: its timerfd implementation rejects `CLOCK_BOOTTIME`. S1L's `libandroid_servers.so` retries that clock with `CLOCK_BOOTTIME_ALARM`, which preserves elapsed-time deadlines but can wake the device for non-wakeup alarms and use more battery. This compatibility path is not runtime-verified; adding the upstream kernel fix is preferable.

## Hardware status

- Display uses the board framebuffer gralloc/HWC path and SwiftShader software rendering. Mali GPU acceleration is not integrated. The factory Mali stack imports Android 4.4 private graphics and ION symbols that do not resolve against this Android 9 system.
- Touch uses the original kernel input drivers. Earlier logs include a failed Hynitron `hyn_ts` probe; the active panel/controller and touch behavior need a fresh device log and screen test.
- Wi-Fi firmware and MTK NL80211 private commands are included in the build. Android 9's Java Wi-Fi framework still needs its HIDL supplicant transport; the stock kernel exposes no independent `/dev/hwbinder` context. An earlier stock-kernel run also reported `NL80211_CMD_GET_PROTOCOL_FEATURES` as unsupported, so Wi-Fi is not runtime-ready.
- Bluetooth has a compiled Android 9 passthrough adapter and the generic factory driver library. Static dependency checks pass; controller initialization, pairing and audio have not been tested on the Z1.
- Audio selects the factory MT6572 primary HAL. Speaker, microphone, headset and suspend/resume behavior need device testing.
- The keypad map assigns Back, Home and Recents to the physical keys, and `qemu.hw.mainkeys=1` hides the virtual navigation bar. The three capacitive key events still need device confirmation.
- S1L's boot helper switches the display to graphics mode but can leave fbcon attached. S1M adds a best-effort `/sys/class/vtconsole` fallback; device testing must confirm the kernel exposes that interface and Android keeps the screen after startup.

Release notes and the two flashable partition assets are in the private [S1L-20261004 GitHub Release](https://github.com/lxjuyn/z1-android9/releases/tag/S1L-20261004). Packages contain only `boot.img` and `system.img.xz`; they exclude NVRAM, calibration partitions, userdata, credentials and captured logs. Decompress `system.img.xz` before writing the raw ext4 system partition with SP Flash Tool.

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

Make one focused fix per commit. Do not commit userdata, raw serial logs, build outputs, images, credentials, access tokens, SSH keys, or personal environment files. Flashable images are published as private GitHub Release assets with checksums; only boot and system partitions are packaged.

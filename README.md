# Z1 Android 9 (LineageOS 16.0)

Single-device Android 9 project for the Jiabo Z1 / MT6572. The repository tracks the Z1 device tree, stock-kernel compatibility patches, build and verification helpers, and a pinned Android source manifest. Large upstream Android sources are synced by `repo`; generated products and images stay out of Git.

## Current diagnostic candidate: S1R

S1R is the latest locally built stock-kernel candidate in `/home/lxj/claude/6572/out/A9_STOCK_S1R_20261004/`. It pairs the unchanged S1Q boot image (factory Z1 3.4.67 kernel plus VT0 display handoff ramdisk) with a rebuilt raw ext4 system image. The boot image is 5,324,800 bytes with SHA-256 `cc6d5fd0259e6659f1bee3baf14a1d2b258a3b8cd3438ab2090ea847d3ce0098`; `system.img` is 943,718,400 bytes with SHA-256 `e6a8079707a0787670c92a8f89ac82ceb992ae410d18884a3477f96873f23f45`. The read-only ext4 check passed. Full details and per-file checksums are in that directory's `README.md`, `IMAGE-METADATA.json`, and `SHA256SUMS`.

S1R includes the current audio policy, the separately defined physical keypad and capacitive navigation maps, and the MT6572 Wi-Fi private-command integration. It has **not** been runtime-verified on the device and is not a finished daily-use ROM. Wi-Fi framework transport and kernel NL80211 support remain blocked or unverified; GPU acceleration is not integrated; Bluetooth, audio, touch, keys, and display handoff still need device checks. S1R has not yet been uploaded to the private GitHub Release.

## Previous display handoff candidate: S1Q

S1Q pairs the S1P system image with a boot ramdisk that creates `/dev/tty0`, requests `KD_GRAPHICS`, then detaches fbcon as a fallback. It retains the exact Z1 3.4.67 kernel and serial diagnostics. The local package is in `out/A9_STOCK_S1Q_20261004/`: `boot.img` SHA-256 `cc6d5fd0259e6659f1bee3baf14a1d2b258a3b8cd3438ab2090ea847d3ce0098`; raw `system.img` SHA-256 `d63aa528e6eddfe77a01358bf0d25af3a663188015e5ef9415ae30d3d48baee5`. Both hashes were checked against `SHA256SUMS`; the boot image is 5,324,800 bytes and the system image is 943,718,400 bytes.

The display change is based on earlier boot logs and still needs a fresh Z1 boot to confirm that the kernel text no longer overwrites Android. S1Q's system image is unchanged from S1P, so it contains the separate keypad and capacitive-navigation key layouts but **does not include** the later Z1-specific audio-policy routing change (`a167005`). S1Q is a diagnostic candidate, not a finished daily-use ROM.

The S1L Release remains an older two-partition candidate; its boot image predates the S1Q display handoff fix.

## S1L base system status

S1L uses the original Z1 3.4.67 kernel. Its `boot.img` and raw 943,718,400-byte ext4 `system.img` passed image, filesystem, init and ELF dependency checks. It has not been boot-tested on the device after the current Bluetooth and Wi-Fi integration. The SELinux policy is permissive version 26 with unknown rules allowed, so this is a controlled bring-up candidate, not a daily-use build.

An earlier run with this factory kernel repeatedly aborted `system_server`: its timerfd implementation rejects `CLOCK_BOOTTIME`. S1L's `libandroid_servers.so` retries that clock with `CLOCK_BOOTTIME_ALARM`, which preserves elapsed-time deadlines but can wake the device for non-wakeup alarms and use more battery. This compatibility path is not runtime-verified; adding the upstream kernel fix is preferable.

## Hardware status

- Display uses the board framebuffer gralloc/HWC path and SwiftShader software rendering. Mali GPU acceleration is not integrated. The factory Mali stack imports Android 4.4 private graphics and ION symbols that do not resolve against this Android 9 system.
- Touch uses the original kernel input drivers. Earlier logs include a failed Hynitron `hyn_ts` probe; the active panel/controller and touch behavior need a fresh device log and screen test.
- Wi-Fi firmware and MTK NL80211 private commands are included in the build. Android 9's Java Wi-Fi framework still needs its HIDL supplicant transport; the stock kernel exposes no independent `/dev/hwbinder` context. An earlier stock-kernel run also reported `NL80211_CMD_GET_PROTOCOL_FEATURES` as unsupported, so Wi-Fi is not runtime-ready.
- Bluetooth has a compiled Android 9 passthrough adapter and the generic factory driver library. Static dependency checks pass; controller initialization, pairing and audio have not been tested on the Z1.
- S1Q selects the MT6572 primary HAL with the older generic speaker/microphone policy. Source commit `a167005` adds Z1 headset and earpiece routes for the next system build; audio routing and suspend/resume still need device testing.
- Separate `mtk-kpd` and `mtk-tpd` layouts map the keypad and capacitive navigation events; `qemu.hw.mainkeys=1` hides the software navigation bar. Actual board events still need device confirmation.
- Previous stock-kernel logs showed the graphics-mode request failed because `/dev/tty0` was missing, so the fbcon fallback could not be trusted. The stock display helper now creates VT0 before requesting `KD_GRAPHICS`, then runs the fbcon detach; serial logging remains enabled. This still needs a fresh Z1 boot check.

Release notes and the two flashable partition assets are in the private [S1L-20261004 GitHub Release](https://github.com/lxjuyn/z1-android9/releases/tag/S1L-20261004). Packages contain only `boot.img` and `system.img.xz`; they exclude NVRAM, calibration partitions, userdata, credentials and captured logs. Decompress `system.img.xz` before writing the raw ext4 system partition with SP Flash Tool.

## Source and builds

A clean checkout uses this repository as the Repo manifest and syncs the pinned Android/LineageOS projects plus the current `main` branch of the Z1 device tree. The manifest also links the device tree's root `Android.mk` into its parent directory so legacy Make discovers the Z1 modules on a clean sync:

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

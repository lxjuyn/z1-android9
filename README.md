# Z1 Android 9 (LineageOS 16.0)

Single-device Android 9 project for the Jiabo Z1 / MT6572. The repository tracks
the device tree, Linux 7.0-rc7 board support, Android patching and packaging
helpers, host-side verification tools, and a pinned Android source manifest.
Large upstream trees and generated images remain outside Git.

[Documentation index](docs/README.md) | [简体中文](README_zh-CN.md)

## Current recovery candidate

The latest host-verified recovery pair is documented in
[Z1 Recovery and Acceptance Plan](docs/Z1_RECOVERY_PLAN_20261112.md). It has
**not** been flashed to a recovered Z1, so the following is a host artifact
identity and test result, not a claim that the device is fixed.

| Part | Identity |
|---|---|
| `boot.img` | 5,982,208 bytes; SHA-256 `ef5f07e4459b6d2fcf42b64a50202349b00e80a979274f85bf9101f1052f1468` |
| Recommended `system.img` | 943,718,400 bytes; SHA-256 `833a65f23ceada548339d9f48db4765931136698755d00c9b67f2f4de12cc8dd` |
| Host validation | 116 tests passed, 1 skipped because its historical log was unavailable |
| Device validation | Not performed in this recovery round |

The boot image is derived from the known `cmpfix` baseline. It keeps the r14
graphics binaries and zImage, while adding the storage gate, checked installer,
and corrected first-boot audio module ordering. The system image is the
audited peripheral candidate. Use the recovery plan for exact paths, the
mandatory read-only evidence step, flash order, and acceptance criteria.

The device still needs a storage and startup acceptance run before the project
can call it recovered. Prior boot logs contain system linker failures, ext4
errors, and service failures at different times; do not use one error as the
root cause of every earlier crash. The recovery candidate deliberately leaves
the lab root command channel available, so `/data` hooks must be inspected
before writing a recovery baseline.

## Current capability boundary

| Area | Status |
|---|---|
| Display and graphics | DRM/OVL and the HWC path have prior onboard evidence. The recovery candidate preserves the r14 binaries, but display and GPU rendering still need a clean post-recovery run. |
| CPU / GPU frequency | The kernel retains the tested DVFS configuration and a 416 MHz GPU performance setting. One failed 491.52 MHz experiment does not prove a physical silicon limit. |
| Touch, keys, power | Earlier board work exists, but the recovery candidate must be retested from a clean storage state. |
| Wi-Fi / Bluetooth / GPS / FM | The deployed `wlan_gen2.ko` identity is pinned, while its exact historical source is still missing. Registration, scan, association, Bluetooth and FM behavior remain separate acceptance items. |
| Audio | First-boot file installation now precedes module startup, but the loader runs in the background and has not produced fresh speaker, headset, or recording evidence. |
| Storage and Android boot | Host tests cover the new gates and installer. Real mounts, properties, journal handling, module loading, and `sys.boot_completed` remain unverified until the recovery run. |

## Repository layout

- `mainline/patches/`, `drivers/`, `graphics/`, and `network/` contain Linux 7
  board-support patches, drivers, Android-facing graphics code, and connectivity
  work.
- `rootdir/`, `keylayout/`, `art_patches/`, `native_path_patches/`, and
  `sepolicy/` contain Android device components.
- `scripts/` contains tracked build, flash, recovery, probing, and test tools.
- `docs/` contains the current recovery plan, subsystem findings, and dated
  historical records.
- `manifests/` pins the upstream Android projects consumed by `repo`.
- `stock_patches/` records patches applied to the synchronized Android tree.

Flight images and large source trees are not committed. Publish any future
Release only after recording the exact boot/system pair, SHA-256 values, host
validation, and device acceptance result.

## Source and build

A clean checkout uses this repository as the Repo manifest:

```sh
repo init -u https://github.com/lxjuyn/z1-android9.git -b main -m default.xml
repo sync -j4
python3 device/jiabo/z1/scripts/apply_stock_patches.py --source "$PWD"
```

`manifests/lineage16-pinned.xml` pins the upstream revisions used by this
workspace. `stock_patches/manifest.json` records each patch base and result
SHA-256. Set `LOS_ROOT` and `HOST_TOOLS`, then run
`scripts/build_z1.sh systemimage`.

Host checks are necessary but do not replace board acceptance. In particular,
shell parsing, archive integrity, image hashes, and simulated mount failures do
not prove that a device mounted the expected block device, loaded a module, or
completed Android startup.

## Safety rules

Keep boot and system images paired by their published hashes. Do not mix
artifacts from different recovery rounds. Do not commit user data, NVRAM,
calibration data, serial or logcat captures, credentials, tokens, SSH keys, or
personal environment files. Normal boot and download mode require different USB
connection steps on this board; follow the operational document for the exact
candidate.

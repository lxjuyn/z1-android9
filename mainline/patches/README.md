# Linux 7 kernel patch set for Jiabo Z1 Android 9

Apply these patches from the matching Linux kernel source root with `patch -p1`. Apply them in numeric order. `0003` and `0004` both update `tools/z1_make_bootimg.sh`, so `0004` must follow `0003`.

## 0001 — Remove `tty0` from Z1 chosen bootargs

The boot image routes kernel printk to `tty0` and `ttyS0`. Android and fbcon share the LK-provided simple framebuffer, so tty0 text can overwrite the Android display. This patch removes only `console=tty0`. It retains `console=ttyS0,921600n8`, `earlycon=mtk8250,0x11005000`, and the other DTS bootargs.

This DTS change does not add or remove an ACM service or helper. A boot experiment still needs a separately verified ramdisk, and only a board run can confirm stable display and runtime USB serial logs.

## 0002 — Android 9 Linux 7 kernel configuration

This records the M7/M9 Linux 7 kernel configuration from revision `c6501138e`. The captured input SHA-256 is `8b0e88cd7a301d016a2f144710e044c9adb436a6ea8d97b33bf59dd7df9e7747`. It normalizes whitespace-only blank lines while preserving Kconfig assignments. It enables Android cgroup support, modular FUSE, IPv6, and netfilter features. `CONFIG_SOUND` and `CONFIG_MTK_CONNECTIVITY` remain disabled; this does not claim audio, Wi-Fi, or Bluetooth support.

## 0003 — Assert the MTK initrd range before packaging

This replaces the legacy packer's nonfatal ROOTFS warning with a hard check. It validates the 512-byte MTK ROOTFS header, declared compressed-body length, and the selected DTB's `linux,initrd-start` / `linux,initrd-end`. It needs Python 3 and `fdtget`. It does not inspect Android init services or add an ACM logger.

## 0004 — Keep the Z1 serial command line aligned

The legacy packer previously defaulted to `console=tty0 console=ttyS0,115200n8`, which disagreed with the Z1 DTS. This changes the default to the full current DTS bootargs and validates both the boot-header command line and selected DTB. Each must contain exactly `console=ttyS0,921600n8` and `earlycon=mtk8250,0x11005000`, with no other `console=` or `earlycon` argument. An override via `Z1_CMDLINE` cannot bypass these checks. This patch leaves the input ramdisk untouched; Android ACM/logger integration is a separate packaging gate documented in `../README.md`.

# Linux7 kernel patch set for Jiabo Z1 Android 9

Apply these patches from the matching Linux kernel source root with `patch -p1`.

## 0001 — Remove `tty0` from Z1 chosen bootargs

The M7/M8 boot image routes kernel printk to both `tty0` and `ttyS0`. Android and fbcon share the LK-provided simple framebuffer, so tty0 text can overwrite the Android display. This patch removes only the `console=tty0` argument. It retains `console=ttyS0,921600n8`, `earlycon=mtk8250,0x11005000`, and every other bootarg.

The USB ACM boot diagnostic relay reads `/dev/kmsg` independently in initramfs; preserve it. This is a reversible display A/B candidate, not a proven fix until the device confirms stable display and USB ACM logs. Do not change the kernel image, ramdisk, or other boot arguments in the same experiment.

## 0002 — Android 9 Linux 7 kernel configuration

Apply after `0001` from the matching Linux kernel source root. This records the M7/M9 Linux 7 kernel configuration from kernel revision `c6501138e`. Captured build input SHA-256: `8b0e88cd7a301d016a2f144710e044c9adb436a6ea8d97b33bf59dd7df9e7747`. The patch normalizes whitespace-only blank lines, producing `8b0e88cd7a301d016a2f144710e044c9adb436a6ea8d97b33bf59dd7df9e7747`, while preserving every Kconfig assignment. It enables Android cgroup support, modular FUSE, IPv6, and netfilter features. `CONFIG_SOUND` and `CONFIG_MTK_CONNECTIVITY` remain disabled; this does not claim audio, Wi-Fi, or Bluetooth support.

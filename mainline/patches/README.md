# Linux7 kernel patch set for Jiabo Z1 Android 9

Apply these patches from the matching Linux kernel source root with `patch -p1`.

## 0001 — Remove `tty0` from Z1 chosen bootargs

The M7/M8 boot image routes kernel printk to both `tty0` and `ttyS0`. Android and fbcon share the LK-provided simple framebuffer, so tty0 text can overwrite the Android display. This patch removes only the `console=tty0` argument. It retains `console=ttyS0,921600n8`, `earlycon=mtk8250,0x11005000`, and every other bootarg.

The USB ACM boot diagnostic relay reads `/dev/kmsg` independently in initramfs; preserve it. This is a reversible display A/B candidate, not a proven fix until the device confirms stable display and USB ACM logs. Do not change the kernel image, ramdisk, or other boot arguments in the same experiment.

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

## 0005 — Z1 R3d: touch VGP1 always-on + dual I2C1 pin groups + I2C0 enable + bootargs baseline

Four DTS-only changes for the CST836U@0x15 no-ACK touch hunt (kernel tree working copy, against the tree HEAD where bootargs still carried `console=tty0` and I2C1 had the single 113/114 group):

1. `&mt6323_vgp1_reg { regulator-always-on; }` — root-cause fix. Board evidence 2026-07-20 (four in-circuit measurements): vgp1 raw=0x0000 sw_en=0, the rail never powered up while VIO18 was alive. The touch AVDD28 is VGP1 (placeholder node reference + MT6580 sister-board vendor config both point here), so the dead rail is the prime suspect for the missing ACK at 0x15.
2. I2C1 dual pin group: keep `i2c1_pins` (113/114, SCL_1_0/SDA_1_0, pinmux only) and add `i2c1_pins_b` (89/90, SCL_1_1/SDA_1_1) with `bias-pull-up`; `&i2c1` references BOTH groups in `pinctrl-0` so the runtime hunt (z1_gpio_hunt) can see which mux answers. NO bias on 113/114 and 105/106: PULLSEL/PUPD share `mt6572_pin_pupd_range` (pinctrl-mt6572.c:203-217, share note :259-260), which covers 87-104 then jumps to 107-112/115-127 — those four pins have no pull-register mapping and a bias property fails pinconf apply with -ENOTSUPP (ten-run forensic 5-2 re-check verdict).
3. I2C0 enable: new `i2c0_pins` (105/106, pinmux only, same no-bias rule) + `&i2c0 { status="okay"; clock-frequency=<400000>; }`.
4. bootargs set to the exact verified baseline string: dropped `console=tty0`, added `firmware_class.path=/system/etc/firmware`.

Verification (this revision, host build only): `make ARCH=arm CROSS_COMPILE=arm-linux-gnueabihf- dtbs` compiled clean; dtc round-trip of the new DTB asserts (a) bootargs byte-identical to the baseline string (empty diff), (b) i2c@1100a000 `pinctrl-0` carries both phandles with `bias-pull-up` present only on the 89/90 group, (c) i2c@11009000 `status="okay"`, (d) a phandle-normalized decompile diff against the R3c baseline DTB (`/home/lxj/claude/6572/out/z1_r3c/mt6572-z1.dtb`) shows no differences outside the I2C nodes, the pin groups, the vgp1 regulator block, bootargs, and `linux,initrd-end`. No board run has happened for this DTB yet; the touch ACK theory stays unproven until the R3d image boots.

## 0006 — Z1 R3e: pstore ramoops carveout + `printk.devkmsg=on` + I2C aliases

Audit-v2 parallel items, DTS-only on top of 0005 (kernel tree working copy):

1. `reserved-memory/ramoops@be200000` (A8-3): 1 MiB `no-map` carveout so a panic leaves a pstore corpse instead of nothing (R3b crashes were forensically blind). `record-size=0x20000 console-size=0x40000 pmsg-size=0x20000 ecc-size=0`. Region math: connsys owns be000000-be100000, framebuffer_reserved starts at bf400000, so be200000-be300000 sits in the free gap, inside the 0x80000000+0x40000000 DRAM window and clear of the CMA `alloc-ranges` (0x90000000+0x10000000). `reg` is TWO cells — this reserved-memory node declares `#address-cells/#size-cells = 1` like the existing connsys/framebuffer nodes; the audit's `<0x0 addr 0x0 size>` 4-cell sketch would misparse under those cells. `CONFIG_PSTORE_RAM=y` is already in the R3 kernel config.
2. bootargs `+= printk.devkmsg=on` (A8-1): R3b's 15 s heartbeat "stopped" because /dev/kmsg writers get ratelimited into silent drops; devkmsg=on removes the per-client ratelimit. The bootargs change must be paired with `z1_make_r3_aggressive.py --cmdline` so the boot header fields and the DTB carry the identical string (enforced by the builder's final parse_boot readback).
3. `aliases { i2c0 = &i2c0; i2c1 = &i2c1; }` (A10-3): pin the I2C bus numbers against probe-order drift so the touch hunt scripts always aim at the same /dev/i2c-N.
4. `linux,initrd-end` placeholder moved to the R3d round's actual value (0x842dfa28); the builder's patch_dtb recomputes it on every package, this only keeps the source aligned so R3e-vs-R3d decompile diffs stay clean.

Verification (host build only, 2026-10-26): `make ARCH=arm CROSS_COMPILE=arm-linux-gnueabihf- dtbs` compiled clean; fdtget reads bootargs byte-identical to the R3e string; decompile diff against the R3d DTB (`/home/lxj/claude/6572/out/z1_r3d/mt6572-z1.dtb`, sha 76d7e5d3…) shows ONLY the aliases lines, the bootargs line, and the ramoops node; interval-overlap assertion over every fixed reserved region + CMA alloc-ranges passes. Builder end-to-end: `--dtb <R3e dtb> --cmdline <R3e string>` builds a 6,191,104-byte boot image (≤6 MiB) with initrd-end recomputed to 0x842e0e80, header cmdline field == DTB bootargs == the R3e string, and the default (no --cmdline) path reproduces the R3d header cmdline segment byte for byte. No board run yet.

# Z1 Linux 7 Android 9 mainline work

This directory records the Linux 7 Android 9 patch set and the related Z1 boot-image packaging guard. The kernel patches are documented in [patches/README.md](patches/README.md).

## 0003 — Assert the MTK initrd range before packaging

`patches/0003-mainline-assert-z1-initrd-range.patch` updates `tools/z1_make_bootimg.sh` in the matching Linux 7 kernel source tree. Apply it from that tree's root with `patch -p1`; this patch targets the build script, not kernel source files.

The guard reads the MTK ROOTFS blob's 512-byte header and little-endian compressed-body length, requires the blob length to match the declared header plus body, then reads `/chosen/linux,initrd-start` and `/chosen/linux,initrd-end` from the resolved DTB argument. Both properties must be one 32-bit cell and the end must equal start plus the declared body length. Missing properties, a bad header, or any mismatch stops packaging before the appended kernel or output boot image is written.

The build host needs `python3` and `fdtget`. The guard validates the exact RAMDISK and DTB selected by positional arguments or `Z1_RAMDISK` / `Z1_DTB`; it does not edit DTS/DTB or ramdisk contents and does not establish that the image boots on hardware.

#!/usr/bin/env python3
"""Split a packed Z1 Android boot.img into its three payloads.

Writes <outdir>/zImage, <outdir>/dtb and <outdir>/ramdisk.rootfs (512-byte MTK
ROOTFS header + compressed cpio), so a single payload can be changed and the
image re-packed with the validated tools:

    z1_ramdisk_edit.py    <ramdisk.rootfs> <ramdisk.new>
    fdtput -t x dtb /chosen linux,initrd-end <0x84100000 + new body size>
    z1_make_android9_bootimg.py <ramdisk.new> out/boot.img \
        --zimage zImage --dtb patched_dtb

Nothing here patches anything; it only verifies structure and extracts.
The kernel section is expected to be (zImage || appended DTB), because Z1's LK
cannot consume a separate --dt section.
"""

import argparse
import gzip
import hashlib
import struct
import sys
from pathlib import Path

PAGE_SIZE = 2048
MTK_HEADER_SIZE = 512
MTK_MAGIC = b"\x88\x16\x88\x58"
FDT_MAGIC = b"\xd0\x0d\xfe\xed"
INITRD_START = 0x84100000


def fail(msg):
    sys.exit(f"ERROR: {msg}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("boot_img", type=Path)
    ap.add_argument("outdir", type=Path)
    args = ap.parse_args()

    blob = args.boot_img.read_bytes()
    if blob[:8] != b"ANDROID!":
        fail("not an Android boot image (no ANDROID! magic)")
    ksize, kaddr, rsize, raddr, ssize, saddr, tags, pagesize, dtsize = \
        struct.unpack_from("<9I", blob, 8)
    if pagesize != PAGE_SIZE:
        fail(f"unexpected page size {pagesize}")
    if dtsize:
        fail(f"image carries a separate dt section ({dtsize} bytes); "
             "expected the DTB to be appended to the zImage")
    if ssize:
        fail("unexpected second stage payload")

    kernel_off = PAGE_SIZE
    payload = blob[kernel_off:kernel_off + ksize]
    if payload[:4] != MTK_MAGIC or payload[8:14] != b"KERNEL":
        fail("kernel payload lacks the MTK KERNEL header")
    if struct.unpack_from("<I", payload, 4)[0] != ksize - MTK_HEADER_SIZE:
        fail("MTK KERNEL header length mismatch")
    kernel = payload[MTK_HEADER_SIZE:]

    # The appended DTB ends the kernel section: its FDT totalsize must place it
    # exactly at the end.
    pos = kernel.rfind(FDT_MAGIC)
    if pos < 0:
        fail("no appended DTB (FDT magic) in the kernel section")
    dtb_total = struct.unpack_from(">I", kernel, pos + 4)[0]
    if pos + dtb_total != len(kernel):
        fail(f"FDT totalsize {dtb_total} does not end the section "
             f"(magic at {pos}, section {len(kernel)})")
    zimage, dtb = kernel[:pos], kernel[pos:]

    ramdisk_off = kernel_off + ((ksize + PAGE_SIZE - 1) // PAGE_SIZE) * PAGE_SIZE
    ramdisk = blob[ramdisk_off:ramdisk_off + rsize]
    if ramdisk[:4] != MTK_MAGIC or b"ROOTFS" not in ramdisk[:32]:
        fail("ramdisk is not an MTK ROOTFS blob")
    # The ROOTFS header records the *compressed* body length (the bytes LK
    # actually loads), which is also the value linux,initrd-end must carry.
    body_size = struct.unpack_from("<I", ramdisk, 4)[0]
    if body_size != len(ramdisk) - MTK_HEADER_SIZE:
        fail(f"ROOTFS header length {body_size} != stored body "
             f"{len(ramdisk) - MTK_HEADER_SIZE}")
    decompressed = len(gzip.decompress(ramdisk[MTK_HEADER_SIZE:]))

    args.outdir.mkdir(parents=True, exist_ok=True)
    (args.outdir / "zImage").write_bytes(zimage)
    (args.outdir / "dtb").write_bytes(dtb)
    (args.outdir / "ramdisk.rootfs").write_bytes(ramdisk)

    cmdline = blob[48 + 16:48 + 16 + 512].split(b"\x00")[0].decode()
    print(f"boot header: kernel={ksize} ramdisk={rsize} pagesize={pagesize} dt={dtsize}")
    print(f"cmdline: {cmdline}")
    print(f"zImage {len(zimage)} b  sha256 {hashlib.sha256(zimage).hexdigest()[:16]}")
    print(f"dtb    {len(dtb)} b  sha256 {hashlib.sha256(dtb).hexdigest()[:16]}")
    print(f"rootfs {len(ramdisk)} b  compressed body {body_size} b "
          f"(cpio {decompressed} b)  initrd-end must be "
          f"{INITRD_START + body_size:#x}")
    print(f"wrote {args.outdir}/zImage {args.outdir}/dtb {args.outdir}/ramdisk.rootfs")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Package a Z1 boot.img v0 from an MTK ROOTFS ramdisk and appended DTB.

This only packages and checks an image. It never writes to the device.
"""

import argparse
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import hashlib
from z1_prepare_android9_ramdisk import INITRD_START, check_cpio


ROOT = Path(__file__).resolve().parent.parent
BOOT_LIMIT = 6 * 1024 * 1024
MTK_MAGIC = b"\x88\x16\x88\x58"
MTK_HEADER_SIZE = 512
PAGE_SIZE = 2048


def fail(message):
    raise ValueError(message)


def dt_cell(dtb, name):
    result = subprocess.run(
        ["fdtget", "-t", "x", str(dtb), "/chosen", name],
        check=True, capture_output=True, text=True,
    )
    values = result.stdout.strip().split()
    if len(values) != 1:
        fail(f"DTB /chosen/{name} must contain one 32-bit cell")
    return int(values[0], 16)


def dt_bootargs(dtb):
    result = subprocess.run(
        ["fdtget", "-t", "s", str(dtb), "/chosen", "bootargs"],
        check=True, capture_output=True, text=True,
    )
    return result.stdout.strip()


def check_ramdisk(path):
    blob = path.read_bytes()
    if len(blob) <= MTK_HEADER_SIZE or blob[:4] != MTK_MAGIC:
        fail("ramdisk needs a 512-byte MTK ROOTFS header")
    body_size = len(blob) - MTK_HEADER_SIZE
    if struct.unpack_from("<I", blob, 4)[0] != body_size:
        fail("MTK ROOTFS header length differs from ramdisk body length")
    if blob[8:14] != b"ROOTFS":
        fail("MTK ramdisk payload is not named ROOTFS")
    body = blob[MTK_HEADER_SIZE:]
    check_cpio(body)
    return blob, body_size


def check_image(blob, kernel, ramdisk):
    if len(blob) > BOOT_LIMIT:
        fail(f"image is {len(blob)} bytes; boot partition limit is {BOOT_LIMIT}")
    if blob[:8] != b"ANDROID!":
        fail("output lacks ANDROID! magic")
    kernel_size, kernel_addr, ramdisk_size, ramdisk_addr, second_size, \
        second_addr, tags_addr, page_size, dt_size = \
        struct.unpack_from("<9I", blob, 8)
    if page_size != PAGE_SIZE or second_size or dt_size:
        fail("output is not the expected v0 layout with appended DTB")
    if (kernel_addr, ramdisk_addr, second_addr, tags_addr) != (
            0x10008000, 0x11000000, 0x10F00000, 0x10000100):
        fail("output boot header has unexpected load addresses")
    if kernel_size != len(kernel) + MTK_HEADER_SIZE:
        fail("boot header kernel size differs from appended kernel payload")
    if ramdisk_size != len(ramdisk):
        fail("boot header ramdisk size differs from ROOTFS input")
    payload = blob[PAGE_SIZE:PAGE_SIZE + kernel_size]
    if payload[:4] != MTK_MAGIC or payload[8:14] != b"KERNEL":
        fail("kernel payload lacks MTK KERNEL header")
    if struct.unpack_from("<I", payload, 4)[0] != len(kernel):
        fail("MTK KERNEL header has wrong payload length")
    if payload[MTK_HEADER_SIZE:] != kernel:
        fail("output kernel payload differs from zImage plus DTB")
    ramdisk_offset = PAGE_SIZE + ((kernel_size + PAGE_SIZE - 1) // PAGE_SIZE) * PAGE_SIZE
    if blob[ramdisk_offset:ramdisk_offset + ramdisk_size] != ramdisk:
        fail("output ramdisk differs from input ROOTFS blob")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ramdisk", type=Path, help="512-byte MTK ROOTFS header plus compressed cpio")
    parser.add_argument("output", type=Path, help="new boot.img path; existing files are rejected")
    parser.add_argument("--zimage", type=Path, default=ROOT / "fork_linux/arch/arm/boot/zImage")
    parser.add_argument("--dtb", type=Path, default=ROOT / "fork_linux/arch/arm/boot/dts/mediatek/mt6572-z1.dtb")
    parser.add_argument("--linux-diagnostic", action="store_true",
                        help="allow the existing Linux diagnostic bootargs for M1 packaging")
    args = parser.parse_args()

    for path in (args.zimage, args.dtb, args.ramdisk):
        if not path.is_file():
            fail(f"missing input: {path}")
    if args.output.exists():
        fail(f"output already exists: {args.output}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    ramdisk, body_size = check_ramdisk(args.ramdisk)
    start = dt_cell(args.dtb, "linux,initrd-start")
    end = dt_cell(args.dtb, "linux,initrd-end")
    if start != INITRD_START:
        fail(f"DTB initrd-start {start:#x} != required {INITRD_START:#x}")
    if end != start + body_size:
        fail(f"DTB initrd-end {end:#x} != start {start:#x} + body {body_size:#x}")
    bootargs = dt_bootargs(args.dtb)
    if not args.linux_diagnostic and "androidboot.hardware=z1" not in bootargs.split():
        fail("DTB bootargs must contain androidboot.hardware=z1 for Android init")
    kernel = args.zimage.read_bytes() + args.dtb.read_bytes()
    mkbootimg = ROOT / "build/mtkbootimg/mkbootimg"
    if not mkbootimg.is_file():
        fail(f"missing mkbootimg: {mkbootimg}")

    with tempfile.TemporaryDirectory(prefix="z1-android9-boot-") as tempdir:
        appended = Path(tempdir) / "zImage-appended-dtb"
        appended.write_bytes(kernel)
        with tempfile.NamedTemporaryFile(prefix=".z1-boot-", suffix=".img",
                                         dir=args.output.parent, delete=False) as output_tmp:
            temporary_output = Path(output_tmp.name)
        try:
            subprocess.run([
                str(mkbootimg), "--kernel", str(appended), "--ramdisk", str(args.ramdisk),
                "--base", "0x10000000", "--kernel_offset", "0x8000",
                "--ramdisk_offset", "0x1000000", "--pagesize", str(PAGE_SIZE),
                "--mtk", "1", "--cmdline", bootargs, "-o", str(temporary_output),
            ], check=True)
            image = temporary_output.read_bytes()
            check_image(image, kernel, ramdisk)
            os.link(temporary_output, args.output)
        finally:
            temporary_output.unlink(missing_ok=True)

    print(f"{args.output}: {len(image)} bytes, sha256 {hashlib.sha256(image).hexdigest()}")
    print(f"DTB initrd range: {start:#x}..{end:#x}; body {body_size} bytes")
    print("Packaging checks passed; device boot has not been verified.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

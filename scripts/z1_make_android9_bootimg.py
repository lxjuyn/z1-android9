#!/usr/bin/env python3
"""Package a Z1 boot.img v0 from an MTK ROOTFS ramdisk and appended DTB.

This only packages and checks an image. It never writes to the device.
"""

import argparse
import gzip
import lzma
import os
from pathlib import Path
import re
import stat
import struct
import subprocess
import sys
import tempfile
import hashlib
from z1_prepare_android9_ramdisk import INITRD_START, check_cpio
from z1_add_bootdiag import entries


ROOT = Path(__file__).resolve().parent.parent
BOOT_LIMIT = 6 * 1024 * 1024
MTK_MAGIC = b"\x88\x16\x88\x58"
MTK_HEADER_SIZE = 512
PAGE_SIZE = 2048
BOOT_ARGS_SIZE = 512
BOOT_EXTRA_ARGS_SIZE = 1024
# build/mtkbootimg/bootimg.h v0 layout: magic, ten 32-bit header fields,
# board name, cmdline, eight-word ID, then the supplemental cmdline.
BOOT_CMDLINE_OFFSET = 8 + 10 * 4 + 16
BOOT_EXTRA_CMDLINE_OFFSET = BOOT_CMDLINE_OFFSET + BOOT_ARGS_SIZE + 8 * 4


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
        ["fdtget", "-t", "r", str(dtb), "/chosen", "bootargs"],
        check=True, capture_output=True,
    )
    # Raw mode preserves embedded NULs. fdtget adds one newline after the
    # property bytes; remove only that delimiter:
    # strip() would silently change a valid (if unusual) leading/trailing space.
    output = result.stdout
    if not output.endswith(b"\n"):
        fail("fdtget did not return /chosen/bootargs property bytes")
    property_bytes = output[:-1]
    if not property_bytes.endswith(b"\x00"):
        fail("DTB /chosen/bootargs is not NUL-terminated")
    bootargs = property_bytes[:-1]
    if b"\x00" in bootargs or b"\n" in bootargs or b"\r" in bootargs:
        fail("DTB /chosen/bootargs contains an ambiguous NUL or line break")
    try:
        return bootargs.decode("utf-8")
    except UnicodeDecodeError:
        fail("DTB /chosen/bootargs is not valid UTF-8")


def check_boot_header_cmdline(blob, expected_cmdline):
    """Check both legacy v0 command-line fields against the exact DTB string.

    The repository's MTK mkbootimg writer stores up to 511 bytes plus a NUL
    in cmdline, then stores any remainder in extra_cmdline (1024 bytes).
    Its struct is zero-initialized, so the unused bytes in both fields must
    also remain zero. This parser is deliberately pure for static validation.
    """
    if isinstance(expected_cmdline, str):
        expected = expected_cmdline.encode("utf-8")
    elif isinstance(expected_cmdline, bytes):
        expected = expected_cmdline
    else:
        fail("expected boot command line must be str or bytes")
    if b"\x00" in expected:
        fail("expected boot command line contains an embedded NUL")
    if len(expected) > BOOT_ARGS_SIZE + BOOT_EXTRA_ARGS_SIZE - 2:
        fail("DTB boot command line exceeds the MTK v0 header capacity")

    extra_end = BOOT_EXTRA_CMDLINE_OFFSET + BOOT_EXTRA_ARGS_SIZE
    if len(blob) < extra_end:
        fail("boot image is too short to contain the MTK v0 command-line fields")

    main_expected = expected[:BOOT_ARGS_SIZE - 1].ljust(
        BOOT_ARGS_SIZE - 1, b"\x00") + b"\x00"
    extra_expected = expected[BOOT_ARGS_SIZE - 1:].ljust(
        BOOT_EXTRA_ARGS_SIZE, b"\x00")
    main_actual = blob[BOOT_CMDLINE_OFFSET:BOOT_CMDLINE_OFFSET + BOOT_ARGS_SIZE]
    extra_actual = blob[BOOT_EXTRA_CMDLINE_OFFSET:extra_end]
    if main_actual != main_expected:
        fail("boot header cmdline field differs from DTB /chosen/bootargs")
    if extra_actual != extra_expected:
        fail("boot header extra_cmdline field differs from DTB /chosen/bootargs")


def canonical_ramdisk_path(name):
    while name.startswith(b"./"):
        name = name[2:]
    while name.startswith(b"/"):
        name = name[1:]
    return name.decode()


ADB_DEBUG_BLOCK = b"""\n# Debug-only ADB beside the independent diagnostic ACM. No ADB keys are added.\non init\n    mkdir /dev/usb-ffs 0755 root root\n    mkdir /dev/usb-ffs/adb 0770 shell shell\n    mount functionfs adb /dev/usb-ffs/adb uid=2000,gid=2000,rmode=0770,fmode=0660\n    start adbd\n\nservice adbd /system/bin/adbd\n    class core\n    socket adbd stream 660\n    user shell\n    group shell\n    disabled\n    seclabel u:r:adbd:s0\n\n"""


def check_android_init_rcs(index, allow_adb=False):
    """Reject stale Android USB init rules from the final packaged ramdisk."""
    if "init.rc" not in index:
        fail("final Android ramdisk lacks root init.rc")
    root_fields, _ = index["init.rc"]
    if not stat.S_ISREG(root_fields[1]):
        fail("final Android ramdisk root init.rc is not a regular file")

    generic_usb_rcs = {"init.usb.rc", "init.usb.configfs.rc"}
    for path in index:
        if path.rsplit("/", 1)[-1] in generic_usb_rcs:
            fail(f"final Android ramdisk must not contain generic USB rc {path}")

    forbidden_gadget_markers = (
        b"/config/usb_gadget/", b"/sys/kernel/config/usb_gadget/",
        b"/sys/class/android_usb/", b"/dev/usb-ffs/", b"functionfs",
        b"ffs.adb",
    )
    forbidden_rc = re.compile(
        rb"^\s*(?:service\s+adbd(?:\s|$)|start\s+adbd(?:\s|$)|"
        rb"setprop\s+ctl\.start\s+adbd(?:\s|$))|"
        rb"^\s*on\b[^\n]*\bproperty:(?:persist\.)?sys\.usb\.", re.M,
    )
    for path, (_, data) in index.items():
        if not path.endswith(".rc"):
            continue
        # init treats '#' as a comment marker; scan active text only.
        if path == "init.z1.rc" and allow_adb:
            if data.count(ADB_DEBUG_BLOCK) != 1:
                fail("ADB debug exception requires exactly one approved init block")
            data = data.replace(ADB_DEBUG_BLOCK, b"\n", 1)
        active = b"\n".join(line.split(b"#", 1)[0]
                             for line in data.splitlines())
        if forbidden_rc.search(active):
            fail(f"final Android ramdisk has an ADB/sys.usb init rule in {path}")
        if any(marker in active for marker in forbidden_gadget_markers):
            fail(f"final Android ramdisk has an Android gadget binding in {path}")
        if re.search(rb"\budc\b", active, re.I):
            fail(f"final Android ramdisk references a USB UDC in {path}")

        for line in active.splitlines():
            fields = line.split()
            if fields and fields[0] == b"import" and len(fields) >= 2:
                if b"init.usb" in fields[1]:
                    fail(f"final Android ramdisk imports a generic USB rc in {path}")
            if len(fields) >= 2 and fields[0] == b"setprop":
                prop = fields[1]
                value = fields[2] if len(fields) >= 3 else b""
                if prop == b"sys.usb.configfs":
                    fail(f"final Android ramdisk selects configfs in {path}")
                if prop in (b"sys.usb.config", b"persist.sys.usb.config") and value != b"none":
                    fail(f"final Android ramdisk enables a generic USB config in {path}")


def check_android_diagnostics(body, allow_adb=False):
    if body.startswith(b"\x1f\x8b"):
        archive = gzip.decompress(body)
    elif body.startswith(b"\xfd7zXZ\x00"):
        archive = lzma.decompress(body)
    else:
        fail("Android ramdisk body must be gzip or xz cpio")
    index = {}
    for name, fields, data in entries(archive):
        key = canonical_ramdisk_path(name)
        if key in index:
            fail(f"duplicate Android ramdisk path: {key}")
        index[key] = (fields, data)
    check_android_init_rcs(index, allow_adb=allow_adb)
    rc = index.get("init.z1.rc", (None, None))[1]
    if rc is None:
        fail("Android ramdisk lacks init.z1.rc; inject ACM diagnostics before MTK wrapping")
    required_rc = (
        b"on init\n    start z1_acm_log\n",
        b"service z1_acm_log /z1_bootdiag --acm\n"
        b"    class core\n    user root\n    group root\n"
        b"    disabled\n    seclabel u:r:init:s0\n",
        b"on post-fs-data\n    start z1_logcat_relay\n",
        b"service z1_logcat_relay /z1_bootdiag --logcat\n"
        b"    class core\n    user root\n    group log\n"
        b"    disabled\n    seclabel u:r:init:s0\n",
    )
    if any(rc.count(fragment) != 1 for fragment in required_rc):
        fail("Android ramdisk must start the USB ACM logger and logcat relay exactly once")
    if (re.search(rb"^\s*start z1_usb_setup\s*$", rc, re.M) or
            re.search(rb"^service z1_usb_setup(?:\s|$)", rc, re.M)):
        fail("Android ramdisk still has the competing ADB gadget setup service")
    helper = index.get("z1_bootdiag")
    if helper is None:
        fail("Android ramdisk lacks /z1_bootdiag")
    fields, data = helper
    if (not stat.S_ISREG(fields[1]) or fields[1] != (stat.S_IFREG | 0o755) or
            fields[2] != 0 or fields[3] != 0 or fields[4] != 1):
        fail("/z1_bootdiag must be a root-owned regular 0755 single-link file")
    if (len(data) < 52 or data[:6] != b"\x7fELF\x01\x01" or data[6] != 1 or
            struct.unpack_from("<H", data, 16)[0] != 2 or
            struct.unpack_from("<H", data, 18)[0] != 40):
        fail("/z1_bootdiag must be a little-endian ARM32 ELF")
    phoff = struct.unpack_from("<I", data, 28)[0]
    if struct.unpack_from("<I", data, 20)[0] != 1:
        fail("/z1_bootdiag has an invalid ELF version")
    phsize, phnum = struct.unpack_from("<HH", data, 42)
    if phoff < 52 or phsize < 32 or not phnum or phoff + phsize * phnum > len(data):
        fail("/z1_bootdiag has an invalid ELF program header table")
    ph_types = [struct.unpack_from("<I", data, phoff + i * phsize)[0]
                for i in range(phnum)]
    if 2 in ph_types or 3 in ph_types:
        fail("/z1_bootdiag must be static and work before /system")
    if 1 not in ph_types:
        fail("/z1_bootdiag has no PT_LOAD segment")


def check_ramdisk(path, require_android_logging=True, allow_adb=False):
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
    if require_android_logging:
        check_android_diagnostics(body, allow_adb=allow_adb)
    return blob, body_size


def check_image(blob, kernel, ramdisk, expected_cmdline=None):
    if len(blob) > BOOT_LIMIT:
        fail(f"image is {len(blob)} bytes; boot partition limit is {BOOT_LIMIT}")
    if blob[:8] != b"ANDROID!":
        fail("output lacks ANDROID! magic")
    kernel_size, kernel_addr, ramdisk_size, ramdisk_addr, second_size, \
        second_addr, tags_addr, page_size, dt_size = \
        struct.unpack_from("<9I", blob, 8)
    if page_size != PAGE_SIZE or second_size or dt_size:
        fail("output is not the expected v0 layout with appended DTB")
    if expected_cmdline is not None:
        check_boot_header_cmdline(blob, expected_cmdline)
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
    ramdisk, body_size = check_ramdisk(args.ramdisk,
                                      require_android_logging=not args.linux_diagnostic)
    start = dt_cell(args.dtb, "linux,initrd-start")
    end = dt_cell(args.dtb, "linux,initrd-end")
    if start != INITRD_START:
        fail(f"DTB initrd-start {start:#x} != required {INITRD_START:#x}")
    if end != start + body_size:
        fail(f"DTB initrd-end {end:#x} != start {start:#x} + body {body_size:#x}")
    bootargs = dt_bootargs(args.dtb)
    if not args.linux_diagnostic:
        tokens = bootargs.split()
        if "androidboot.hardware=z1" not in tokens:
            fail("DTB bootargs must contain androidboot.hardware=z1 for Android init")
        if "console=ttyS0,921600n8" not in tokens or "earlycon=mtk8250,0x11005000" not in tokens:
            fail("Android candidate must retain Z1 UART and earlycon logging")
        if "console=tty0" in tokens:
            fail("Android candidate must not route printk to the shared display framebuffer")
    kernel = args.zimage.read_bytes() + args.dtb.read_bytes()
    mkbootimg = ROOT / "out/host/bin/mtkbootimg"
    mkbootimg_source = ROOT / "build/mtkbootimg/mkbootimg.c"
    mkbootimg_header = ROOT / "build/mtkbootimg/bootimg.h"
    mkbootimg_builder = ROOT / "build/mtkbootimg/build_host.sh"
    if not mkbootimg_source.is_file() or not mkbootimg_header.is_file() or not mkbootimg_builder.is_file():
        fail("missing repository-owned MTK mkbootimg source or host build script")
    if (not mkbootimg.is_file() or
            max(mkbootimg_source.stat().st_mtime_ns, mkbootimg_header.stat().st_mtime_ns,
                mkbootimg_builder.stat().st_mtime_ns) > mkbootimg.stat().st_mtime_ns):
        subprocess.run([str(mkbootimg_builder), str(mkbootimg)], check=True)

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
            check_image(image, kernel, ramdisk, bootargs)
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

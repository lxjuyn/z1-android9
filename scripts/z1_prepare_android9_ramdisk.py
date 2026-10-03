#!/usr/bin/env python3
"""Prepare a LOS16 ramdisk for Z1's MTK LK and patch its DTB initrd range.

Input is the standard compressed cpio ramdisk produced by Android. Output is
an MTK ROOTFS blob plus a copy of the DTB with the exact body length recorded.
The source DTB and source ramdisk are never modified.
"""

import argparse
import gzip
import hashlib
import json
import lzma
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parent.parent
MTK_HEADER_SIZE = 512
INITRD_START = 0x84100000


def fail(message):
    raise ValueError(message)


def dt_cell(dtb, property_name):
    result = subprocess.run(
        ["fdtget", "-t", "x", str(dtb), "/chosen", property_name],
        check=True, capture_output=True, text=True,
    )
    cells = result.stdout.strip().split()
    if len(cells) != 1:
        fail(f"/chosen/{property_name} must have one 32-bit cell")
    return int(cells[0], 16)


def check_cpio(body):
    try:
        if body.startswith(b"\x1f\x8b"):
            cpio = gzip.decompress(body)
        elif body.startswith(b"\xfd7zXZ\x00"):
            cpio = lzma.decompress(body)
        else:
            fail("input ramdisk must be gzip or xz compressed cpio")
    except (OSError, EOFError, lzma.LZMAError) as exc:
        fail(f"ramdisk decompression failed: {exc}")
    check_newc(cpio)


def check_newc(cpio):
    """Reject truncated or malformed archives before they enter a boot image."""
    offset = 0
    init_is_executable = False
    while True:
        if len(cpio) - offset < 110:
            fail("cpio archive is missing a complete header or TRAILER!!!")
        header = cpio[offset:offset + 110]
        magic = header[:6]
        if magic not in (b"070701", b"070702"):
            fail(f"invalid newc/crc cpio magic at offset {offset}")
        if any(byte not in b"0123456789abcdefABCDEF" for byte in header[6:]):
            fail(f"non-hex cpio header field at offset {offset}")
        try:
            fields = [int(header[pos:pos + 8], 16) for pos in range(6, 110, 8)]
        except ValueError:
            fail(f"invalid cpio header field at offset {offset}")
        mode, size, name_size, checksum = fields[1], fields[6], fields[11], fields[12]
        if name_size < 2:
            fail(f"invalid cpio name length at offset {offset}")
        name_start = offset + 110
        name_end = name_start + name_size
        if name_end > len(cpio):
            fail("cpio entry name extends beyond archive")
        name = cpio[name_start:name_end]
        if not name.endswith(b"\0") or b"\0" in name[:-1]:
            fail("cpio entry name is not NUL terminated")
        data_start = (name_end + 3) & ~3
        data_end = data_start + size
        next_offset = (data_end + 3) & ~3
        if next_offset > len(cpio):
            fail(f"cpio entry {name[:-1]!r} extends beyond archive")
        if any(cpio[name_end:data_start]) or any(cpio[data_end:next_offset]):
            fail(f"cpio entry {name[:-1]!r} has nonzero alignment padding")
        data = cpio[data_start:data_end]
        if magic == b"070702":
            if sum(data) & 0xFFFFFFFF != checksum:
                fail(f"cpio CRC mismatch for {name[:-1]!r}")
        elif checksum != 0:
            fail(f"newc entry {name[:-1]!r} has a nonzero CRC field")
        entry_name = name[:-1]
        if entry_name in (b"init", b"./init"):
            init_is_executable = (stat.S_IFMT(mode) == stat.S_IFREG and
                                  bool(mode & 0o111) and size > 0)
        if entry_name == b"TRAILER!!!":
            if size != 0:
                fail("cpio TRAILER!!! has a payload")
            if any(cpio[next_offset:]):
                fail("cpio contains nonzero bytes after TRAILER!!!")
            if not init_is_executable:
                fail("cpio needs a nonempty executable regular /init")
            return
        offset = next_offset


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ramdisk", type=Path, help="standard LOS16 ramdisk.img")
    parser.add_argument("output_dir", type=Path, help="new directory for ROOTFS and patched DTB")
    parser.add_argument("--dtb", type=Path,
                        default=ROOT / "fork_linux/arch/arm/boot/dts/mediatek/mt6572-z1.dtb")
    args = parser.parse_args()
    if not args.ramdisk.is_file() or not args.dtb.is_file():
        fail("ramdisk and DTB must both exist")
    if args.output_dir.exists():
        fail(f"output directory already exists: {args.output_dir}")
    body = args.ramdisk.read_bytes()
    if len(body) > 0xFFFFFFFF - INITRD_START:
        fail("ramdisk exceeds 32-bit initrd address range")
    check_cpio(body)
    if dt_cell(args.dtb, "linux,initrd-start") != INITRD_START:
        fail(f"DTB initrd start must be {INITRD_START:#x} for Z1 LK")
    bootargs = subprocess.run(
        ["fdtget", "-t", "s", str(args.dtb), "/chosen", "bootargs"],
        check=True, capture_output=True, text=True,
    ).stdout.strip().split()
    if "androidboot.hardware=z1" not in bootargs:
        fail("DTB lacks androidboot.hardware=z1")

    args.output_dir.parent.mkdir(parents=True, exist_ok=True)
    tempdir = Path(tempfile.mkdtemp(prefix=".z1-ramdisk-", dir=args.output_dir.parent))
    try:
        header = (b"\x88\x16\x88\x58" + struct.pack("<I", len(body)) +
                  b"ROOTFS" + bytes(26) + bytes([0xFF]) * (MTK_HEADER_SIZE - 40))
        if len(header) != MTK_HEADER_SIZE:
            fail("internal MTK header length error")
        rootfs = tempdir / "ramdisk-mtk.img"
        rootfs.write_bytes(header + body)
        dtb = tempdir / "mt6572-z1.dtb"
        shutil.copyfile(args.dtb, dtb)
        end = INITRD_START + len(body)
        subprocess.run(
            ["fdtput", "-t", "x", str(dtb), "/chosen", "linux,initrd-end", f"{end:x}"],
            check=True, capture_output=True, text=True,
        )
        if dt_cell(dtb, "linux,initrd-end") != end:
            fail("patched DTB initrd end verification failed")
        manifest = {
            "ramdisk_body_bytes": len(body),
            "initrd_start": f"{INITRD_START:#x}",
            "initrd_end": f"{end:#x}",
            "ramdisk_mtk_sha256": hashlib.sha256(rootfs.read_bytes()).hexdigest(),
            "dtb_sha256": hashlib.sha256(dtb.read_bytes()).hexdigest(),
        }
        (tempdir / "package-inputs.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        # mkdir is exclusive: another process cannot make us replace its
        # directory between the initial check and publishing these outputs.
        args.output_dir.mkdir()
        try:
            for name in ("ramdisk-mtk.img", "mt6572-z1.dtb", "package-inputs.json"):
                os.replace(tempdir / name, args.output_dir / name)
        except Exception:
            shutil.rmtree(args.output_dir)
            raise
    finally:
        if tempdir.exists():
            shutil.rmtree(tempdir)
    print(f"{args.output_dir}: ROOTFS body {len(body)} bytes, DT initrd end {end:#x}")
    print("Use ramdisk-mtk.img and mt6572-z1.dtb with z1_make_android9_bootimg.py.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

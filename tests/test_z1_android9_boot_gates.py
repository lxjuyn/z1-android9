"""Regression tests for the Z1 boot image input safety gates."""

import gzip
import lzma
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from z1_make_android9_bootimg import check_image, check_ramdisk
from z1_prepare_android9_ramdisk import check_cpio, check_newc


def entry(name, mode, data=b"", magic=b"070701", checksum=0):
    name = name.encode() + b"\0"
    fields = [1, mode, 0, 0, 1, 0, len(data), 0, 0, 0, 0,
              len(name), checksum]
    record = magic + b"".join(f"{value:08x}".encode() for value in fields) + name
    record += bytes(-len(record) % 4)
    record += data
    return record + bytes(-len(record) % 4)


def archive(init_mode=0o100755, init_data=b"#!/bin/sh\n", crc=False):
    magic = b"070702" if crc else b"070701"
    return (entry("init", init_mode, init_data, magic,
                  sum(init_data) if crc else 0) +
            entry("TRAILER!!!", 0, magic=magic))


class RamdiskGateTests(unittest.TestCase):
    def test_accepts_newc_and_crc_with_executable_init(self):
        for crc in (False, True):
            with self.subTest(crc=crc):
                check_newc(archive(crc=crc) + bytes(64))
                check_cpio(gzip.compress(archive(crc=crc)))
                check_cpio(lzma.compress(archive(crc=crc)))

    def test_rejects_missing_or_invalid_init(self):
        cases = [
            entry("etc/config", 0o100644, b"x") + entry("TRAILER!!!", 0),
            archive(init_mode=0o120777),  # executable symlink is not a regular file
            archive(init_mode=0o100644),
            archive(init_data=b""),
            archive()[:-len(entry("TRAILER!!!", 0))],
        ]
        for cpio in cases:
            with self.subTest(cpio=cpio[:20]):
                with self.assertRaises(ValueError):
                    check_newc(cpio)

    def test_rejects_malformed_archive_and_crc(self):
        valid = archive(crc=True)
        cases = [
            valid[:-1],
            valid + b"x",
            valid[:6] + b"z" + valid[7:],
            valid[:102] + b"00000000" + valid[110:],  # wrong CRC
            entry("init", 0o100755, b"ok", b"070702", 0) +
            entry("TRAILER!!!", 0, magic=b"070702"),
        ]
        for cpio in cases:
            with self.subTest(size=len(cpio)):
                with self.assertRaises(ValueError):
                    check_newc(cpio)

    def test_packager_rejects_compressed_non_archive(self):
        body = gzip.compress(b"070701 incomplete")
        header = (b"\x88\x16\x88\x58" + struct.pack("<I", len(body)) +
                  b"ROOTFS" + bytes(498))
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "ramdisk.img"
            path.write_bytes(header + body)
            with self.assertRaises(ValueError):
                check_ramdisk(path)

    def test_boot_header_addresses_are_checked(self):
        kernel = b"K"
        ramdisk = b"R"
        blob = bytearray(4096 + 512)
        blob[:8] = b"ANDROID!"
        struct.pack_into("<9I", blob, 8, 513, 0x10008000, 1, 0x11000000,
                         0, 0x10F00000, 0x10000100, 2048, 0)
        blob[2048:2052] = b"\x88\x16\x88\x58"
        blob[2056:2062] = b"KERNEL"
        struct.pack_into("<I", blob, 2052, 1)
        blob[2560] = ord("K")
        blob[4096] = ord("R")
        check_image(blob, kernel, ramdisk)
        struct.pack_into("<I", blob, 20, 0x11001000)
        with self.assertRaisesRegex(ValueError, "load addresses"):
            check_image(blob, kernel, ramdisk)


if __name__ == "__main__":
    unittest.main()

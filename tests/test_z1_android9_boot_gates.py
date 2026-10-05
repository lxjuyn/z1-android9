"""Regression tests for the Z1 boot image input safety gates."""

import gzip
import lzma
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from z1_make_android9_bootimg import check_android_diagnostics, check_image, check_ramdisk
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
    root_rc = b"import /init.z1.rc\n"
    return (entry("init", init_mode, init_data, magic,
                  sum(init_data) if crc else 0) +
            entry("init.rc", 0o100644, root_rc, magic,
                  sum(root_rc) if crc else 0) +
            entry("TRAILER!!!", 0, magic=magic))


def bootdiag_elf(program_type=1):
    elf = bytearray(84)
    elf[:7] = b"\x7fELF\x01\x01\x01"
    struct.pack_into("<HHI", elf, 16, 2, 40, 1)
    struct.pack_into("<I", elf, 28, 52)
    struct.pack_into("<HH", elf, 42, 32, 1)
    struct.pack_into("<I", elf, 52, program_type)
    return bytes(elf)


def diagnostic_archive(helper=None, conflict=False):
    rc = (
        b"on init\n    start z1_acm_log\n\n"
        b"service z1_acm_log /z1_bootdiag --acm\n"
        b"    class core\n    user root\n    group root\n"
        b"    disabled\n    seclabel u:r:init:s0\n\n"
        b"on post-fs-data\n    start z1_logcat_relay\n\n"
        b"service z1_logcat_relay /z1_bootdiag --logcat\n"
        b"    class core\n    user root\n    group log\n"
        b"    disabled\n    seclabel u:r:init:s0\n"
    )
    if conflict:
        rc += b"\nservice z1_usb_setup /system/bin/sh /system/bin/z1_usb_setup.sh\n"
    cpio = entry("init", 0o100755, b"#!/bin/sh\n")
    cpio += entry("init.rc", 0o100644, b"import /init.z1.rc\n")
    cpio += entry("init.z1.rc", 0o100644, rc)
    if helper is not None:
        cpio += entry("z1_bootdiag", 0o100755, helper)
    return cpio + entry("TRAILER!!!", 0)


def mtk_ramdisk(body):
    return (b"\x88\x16\x88\x58" + struct.pack("<I", len(body)) +
            b"ROOTFS" + bytes(498) + body)


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

    def test_android_packager_requires_acm_and_logcat_services(self):
        with self.assertRaisesRegex(ValueError, "init.z1.rc"):
            check_android_diagnostics(gzip.compress(archive()))
        with self.assertRaisesRegex(ValueError, "ACM logger and logcat relay"):
            cpio = (entry("init", 0o100755, b"#!/bin/sh\n") +
                    entry("init.rc", 0o100644, b"import /init.z1.rc\n") +
                    entry("init.z1.rc", 0o100644, b"") +
                    entry("TRAILER!!!", 0))
            check_android_diagnostics(gzip.compress(cpio))

    def test_android_packager_requires_static_root_bootdiag(self):
        good = gzip.compress(diagnostic_archive(bootdiag_elf()))
        check_android_diagnostics(good)
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "ramdisk.img"
            path.write_bytes(mtk_ramdisk(good))
            check_ramdisk(path)
            path.write_bytes(mtk_ramdisk(gzip.compress(archive())))
            with self.assertRaisesRegex(ValueError, "init.z1.rc"):
                check_ramdisk(path)
        for program_type in (2, 3):  # PT_DYNAMIC and PT_INTERP
            with self.assertRaisesRegex(ValueError, "must be static"):
                check_android_diagnostics(gzip.compress(
                    diagnostic_archive(bootdiag_elf(program_type))))
        with self.assertRaisesRegex(ValueError, "competing ADB gadget"):
            check_android_diagnostics(gzip.compress(
                diagnostic_archive(bootdiag_elf(), conflict=True)))

    def test_linux_diagnostic_profile_can_use_its_own_serial_transport(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "ramdisk.img"
            path.write_bytes(mtk_ramdisk(gzip.compress(archive())))
            check_ramdisk(path, require_android_logging=False)

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

"""Host-only tests for the original-R9 boot-only takeover packager."""

import gzip
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import z1_repair_r9_boot as repair
from z1_add_bootdiag import encode
from z1_make_android9_bootimg import check_image, check_ramdisk, dt_cell
from z1_make_stock_android9 import ramdisk_index

R9 = ROOT / "r9_extract/Linux7-20261006-r9/boot.img"
EXPECTED_R9 = "1d3b22d0f1b24a32b8ce984c95a06ec4a1d689e27accbbe496f217c591ec663f"


def archive(index):
    return b"".join(r.raw for r in repair.records(gzip.decompress(index)))


class RepairR9BootTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not R9.is_file():
            raise unittest.SkipTest("original R9 boot.img is not available")
        if shutil.which("fdtget") is None or shutil.which("fdtput") is None or shutil.which("dtc") is None:
            raise unittest.SkipTest("device-tree host tools are not available")
        cls.base = repair.parse_boot(R9.read_bytes())
        cls.temp = tempfile.TemporaryDirectory(prefix="z1-r9-host-tests-")
        cls.output = Path(cls.temp.name) / "candidate"
        repair.repair(R9, cls.output, EXPECTED_R9)
        cls.candidate = repair.parse_boot((cls.output / "boot.img").read_bytes())
        cls.before = ramdisk_index([(r.name, r.fields, r.data) for r in repair.records(
            gzip.decompress(cls.base.ramdisk[512:]))])
        cls.after = ramdisk_index([(r.name, r.fields, r.data) for r in repair.records(
            gzip.decompress(cls.candidate.ramdisk[512:]))])

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "temp"):
            cls.temp.cleanup()

    def test_mtk_kernel_header_is_not_treated_as_zimage(self):
        self.assertEqual(self.candidate.kernel[:4], self.base.kernel[:4])
        self.assertEqual(self.candidate.kernel[8:512], self.base.kernel[8:512])
        self.assertEqual(self.base.zimage[:4], bytes.fromhex("4d5a25e2"))
        self.assertGreater(len(self.candidate.zimage), 4_000_000)
        self.assertEqual(self.candidate.zimage, self.base.zimage)
        self.assertEqual(self.candidate.dtb, self.candidate.kernel[512 + len(self.base.zimage):])

    def test_initrd_range_and_rootfs_body_are_recalculated(self):
        for name in ("boot.img", "ramdisk-mtk.img"):
            self.assertTrue((self.output / name).is_file())
        ramdisk, body_size = check_ramdisk(self.output / "ramdisk-mtk.img")
        self.assertEqual(ramdisk, self.candidate.ramdisk)
        self.assertEqual(struct.unpack_from("<I", ramdisk, 4)[0], body_size)
        self.assertEqual(dt_cell(self.output / "mt6572-z1.dtb", "linux,initrd-start"), 0x84100000)
        self.assertEqual(dt_cell(self.output / "mt6572-z1.dtb", "linux,initrd-end"),
                         0x84100000 + body_size)
        self.assertEqual(self.candidate.initrd_end, 0x84100000 + body_size)
        self.assertNotEqual(body_size, len(self.base.ramdisk) - 512)

    def test_packager_validates_kernel_command_line_and_capacity(self):
        check_image((self.output / "boot.img").read_bytes(),
                    self.candidate.zimage + self.candidate.dtb,
                    self.candidate.ramdisk, self.base.cmdline)
        self.assertEqual(self.candidate.cmdline, self.base.cmdline)
        self.assertIn("panic=10", self.candidate.cmdline.split())
        self.assertIn("start watchdogd", self.after["init.z1.rc"][2].decode())
        self.assertLessEqual((self.output / "boot.img").stat().st_size, 6 * 1024 * 1024)

    def test_cpio_change_allowlist_and_metadata(self):
        self.assertEqual(sorted(set(self.after) - set(self.before)),
                         ["z1_display_takeover.sh", "z1_fbcon_takeover.sh"])
        self.assertEqual(set(self.before) - set(self.after), set())
        self.assertEqual([key for key in self.before
                          if self.before[key][2] != self.after[key][2]], ["init.z1.rc"])
        for key in self.before:
            if key != "init.z1.rc":
                self.assertEqual(self.before[key][:2], self.after[key][:2], key)
        _, old_fields, _ = self.before["init.z1.rc"]
        _, new_fields, rc = self.after["init.z1.rc"]
        self.assertEqual(old_fields[:6], new_fields[:6])
        self.assertEqual(old_fields[7:11], new_fields[7:11])
        command = (b"exec u:r:init:s0 root root -- "
                   b"/system/bin/sh /z1_display_takeover.sh")
        action = rc.split(b"on post-fs-data\n", 1)[1].split(b"\non ", 1)[0]
        self.assertEqual(action.count(command), 1)
        self.assertNotIn(b"dexopt", rc)
        self.assertNotIn(b"wallpaper", rc)
        for name in repair.SCRIPT_NAMES:
            _, fields, data = self.after[name]
            self.assertEqual(fields[1:5], [0o100755, 0, 0, 1])
            self.assertEqual(data, (ROOT / "stock" / name).read_bytes())

    def test_unchanged_cpio_records_keep_original_encoding(self):
        before = repair.records(gzip.decompress(self.base.ramdisk[512:]))
        after = repair.records(gzip.decompress(self.candidate.ramdisk[512:]))
        old = [r.raw for r in before if r.key != "init.z1.rc"]
        new = [r.raw for r in after if r.key not in repair.SCRIPT_NAMES and r.key != "init.z1.rc"]
        self.assertEqual(old, new)

    def test_dt_semantics_only_change_initrd_end(self):
        with tempfile.TemporaryDirectory(prefix="z1-r9-dt-test-") as temp:
            before = Path(temp) / "before.dtb"
            after = Path(temp) / "after.dtb"
            before.write_bytes(self.base.dtb)
            after.write_bytes(self.candidate.dtb)
            self.assertEqual(repair.normalized_dts(before), repair.normalized_dts(after))

    def test_rebuild_is_reproducible_and_manifest_hashes_match(self):
        with tempfile.TemporaryDirectory(prefix="z1-r9-repro-") as temp:
            second = Path(temp) / "candidate"
            manifest = repair.repair(R9, second, EXPECTED_R9)
            for name in ("boot.img", "ramdisk-mtk.img", "mt6572-z1.dtb", "manifest.json"):
                self.assertEqual((self.output / name).read_bytes(), (second / name).read_bytes(), name)
                if name != "manifest.json":
                    data = (second / name).read_bytes()
                    self.assertEqual(manifest["outputs"][name]["sha256"], hashlib.sha256(data).hexdigest())
            self.assertEqual(manifest["input_boot"]["sha256"], EXPECTED_R9)
            self.assertFalse(manifest["device_boot_verified"])
            self.assertFalse(manifest["flashing_performed"])
            self.assertFalse(manifest["preserved"]["dexopt_changes"])

    def test_selinux_restore_is_opt_in_and_preserved(self):
        with tempfile.TemporaryDirectory(prefix="z1-r9-restorecon-") as temp:
            output = Path(temp) / "candidate"
            manifest = repair.repair(R9, output, EXPECTED_R9, restorecon_etc=True)
            candidate = repair.parse_boot((output / "boot.img").read_bytes())
            index = ramdisk_index([(r.name, r.fields, r.data) for r in repair.records(gzip.decompress(candidate.ramdisk[512:]))])
            self.assertEqual(index["default.prop"][2], self.before["default.prop"][2] + b"selinux.restorecon_recursive=/system/etc\n")
            self.assertEqual(sorted(manifest["ramdisk_changes"]["modified"]), ["default.prop", "init.z1.rc"])
            self.assertEqual(manifest["preserved"]["selinux_restorecon_recursive"], "/system/etc")
            self.assertIn(b"selinux.restorecon_recursive=/system/etc\n", index["default.prop"][2])

    def test_driver_evidence_requires_complete_opt_in(self):
        with tempfile.TemporaryDirectory(prefix="z1-touch-gate-") as temp:
            with self.assertRaisesRegex(ValueError, "system attachment"):
                repair.repair(R9, Path(temp) / "partial", EXPECTED_R9, touch_probe=ROOT / "r9_extract/Linux7-20261006-r9/boot.img")
            self.assertFalse((Path(temp) / "partial").exists())

    def test_rejects_existing_output_and_wrong_baseline(self):
        with self.assertRaisesRegex(ValueError, "output directory must be new"):
            repair.repair(R9, self.output, EXPECTED_R9)
        with tempfile.TemporaryDirectory(prefix="z1-r9-pin-") as temp:
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                repair.repair(R9, Path(temp) / "candidate", "0" * 64)
            self.assertEqual(list(Path(temp).iterdir()), [])

    def test_rejects_double_injection_without_touching_other_rc_actions(self):
        with self.assertRaisesRegex(ValueError, "already referenced"):
            repair.add_takeover_command(self.after["init.z1.rc"][2])
        before = self.before["init.z1.rc"][2]
        updated = repair.add_takeover_command(before)
        marker = b"    exec u:r:init:s0 root root -- /system/bin/sh /z1_display_takeover.sh\n"
        self.assertEqual(updated.replace(marker, b"", 1), before)


if __name__ == "__main__":
    unittest.main()

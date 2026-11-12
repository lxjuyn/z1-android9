#!/usr/bin/env python3
"""Host-only acceptance tests for the recovery boot builder."""
import gzip
from pathlib import Path
import struct
import unittest

import z1_prepare_recovery_boot as recovery

BASE = Path(__file__).resolve().parents[2] / "out/boot_cmpfix.img"


@unittest.skipUnless(BASE.is_file(), "audited local boot fixture unavailable")
class RecoveryBootTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = BASE.read_bytes()
        cls.header, cls.kh, cls.zimage, cls.dtb, cls.rootfs = recovery.split_boot(cls.raw)
        cls.body, cls.records = recovery.cpio(cls.rootfs)
        cls.files = recovery.contents(cls.body, cls.records)

    def test_reference_identity_and_init_support(self):
        self.assertEqual(recovery.sha(self.raw), recovery.BASE_SHA)
        self.assertIn(b"wait_for_prop", self.files["init"])

    def test_truncation_and_header_mismatch_rejected(self):
        for raw in (self.raw[:4096], b"invalid!" + self.raw[8:]):
            with self.assertRaises(ValueError):
                recovery.split_boot(raw)
        changed = bytearray(self.raw)
        struct.pack_into("<I", changed, 2052, 1)
        with self.assertRaisesRegex(ValueError, "MTK length"):
            recovery.split_boot(changed)

    def test_both_gates_precede_downstream_writers(self):
        rc = recovery.patch_rc(self.files["init.z1.rc"].decode())
        storage = rc.index("wait_for_prop sys.z1.storage_ready 1")
        stage = rc.index("wait_for_prop z1stage.status ready")
        self.assertLess(storage, rc.index("on post-fs-data"))
        self.assertLess(stage, rc.index("start z1_rootd"))
        self.assertLess(stage, rc.index("swapon_all"))
        self.assertLess(stage, rc.index("exec_background"))
        self.assertIn("class core\n    disabled\n", rc[rc.index("service z1_rootd"):])
        self.assertNotIn("/system/bin/cp /hwcomposer.z1.so", rc)

    def test_missing_stage_wait_rejected(self):
        rc = recovery.patch_rc(self.files["init.z1.rc"].decode())
        with self.assertRaisesRegex(ValueError, "installation gate"):
            recovery.verify_rc(rc.replace("    wait_for_prop z1stage.status ready\n", ""))

    def test_unknown_or_already_patched_rc_rejected(self):
        rc = self.files["init.z1.rc"].decode()
        for candidate in (rc.replace("mount_all /fstab.z1", "mount_all /fstab.other"),
                          recovery.patch_rc(rc)):
            with self.assertRaises(ValueError):
                recovery.patch_rc(candidate)

    def test_only_requested_cpio_record_changes(self):
        edited = recovery.edit_rootfs(self.rootfs, {"z1stage_boot.sh": b"#!/system/bin/sh\nexit 1\n"})
        body, records = recovery.cpio(edited)
        files = recovery.contents(body, records)
        self.assertEqual(set(files), set(self.files))
        self.assertEqual([n for n in files if files[n] != self.files[n]], ["z1stage_boot.sh"])
        for a, b in zip(self.records, records):
            if a["name"] != "z1stage_boot.sh":
                self.assertEqual(self.body[a["pos"]:a["file_end"]], body[b["pos"]:b["file_end"]])
        self.assertEqual(struct.unpack_from("<I", edited, 4)[0], len(edited) - 512)

    def test_unknown_record_rejected(self):
        with self.assertRaisesRegex(ValueError, "missing replacement"):
            recovery.edit_rootfs(self.rootfs, {"missing": b""})

    def test_nonzero_trailing_cpio_data_rejected(self):
        raw = self.body + b"unexpected second archive"
        gz = gzip.compress(raw, mtime=0)
        header = bytearray(self.rootfs[:512])
        struct.pack_into("<I", header, 4, len(gz))
        with self.assertRaisesRegex(ValueError, "after cpio"):
            recovery.cpio(bytes(header) + gz)

    def test_pack_preserves_all_payloads(self):
        img = recovery.make_image(self.header, self.kh, self.zimage, self.dtb, self.rootfs)
        header, kh, zi, dt, rd = recovery.split_boot(img)
        self.assertEqual((kh, zi, dt, rd), (self.kh, self.zimage, self.dtb, self.rootfs))
        self.assertLessEqual(len(img), recovery.LIMIT)
        self.assertEqual(len(img) % 2048, 0)


if __name__ == "__main__":
    unittest.main()

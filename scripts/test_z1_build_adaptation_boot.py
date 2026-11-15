#!/usr/bin/env python3
"""Exercise real packing and reverse checks using only new temporary files."""
import copy
import gzip
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest

import z1_build_adaptation_boot as candidate

boot = candidate.boot


def record(name, data, mode=0o100644):
    name_bytes = name.encode() + b"\0"
    fields = [1, mode, 0, 0, 1, 0, len(data), 0, 0, 0, 0, len(name_bytes), 0]
    header = b"070701" + b"".join(f"{n:08x}".encode() for n in fields) + name_bytes
    return boot.pad(header, 4) + boot.pad(data, 4)


def module_elf(info, extra=b""):
    names = b"\0.shstrtab\0.modinfo\0"
    header = bytearray(52)
    header[:7] = b"\x7fELF\x01\x01\x01"
    struct.pack_into("<HHI", header, 16, 1, 40, 1)
    struct.pack_into("<I", header, 32, 52)
    struct.pack_into("<HHH", header, 46, 40, 3, 1)
    table = bytearray(120)
    struct.pack_into("<10I", table, 40, 1, 3, 0, 0, 172, len(names), 0, 0, 1, 0)
    struct.pack_into("<10I", table, 80, 11, 1, 0, 0, 172 + len(names),
                     len(info), 0, 0, 1, 0)
    return bytes(header + table) + names + info + extra


class ModuleIdentityTests(unittest.TestCase):
    def test_exact_modinfo_field(self):
        candidate.check_arm(module_elf(b"license=GPL\0vermagic=" +
                            candidate.MODULE_VERMAGIC + b"\0"), "module.ko")

    def test_prefix_lookalikes_and_duplicate_fields_rejected(self):
        for value in (b"7.0.0-rc7-z1+evil", b"7.0.0-rc7-z1+",
                      candidate.MODULE_VERMAGIC + b"extra"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "vermagic"):
                candidate.check_arm(module_elf(b"vermagic=" + value + b"\0"), "module.ko")
        field = b"vermagic=" + candidate.MODULE_VERMAGIC + b"\0"
        with self.assertRaisesRegex(ValueError, "vermagic"):
            candidate.check_arm(module_elf(field * 2), "module.ko")

    def test_marker_outside_modinfo_and_unterminated_field_rejected(self):
        field = b"vermagic=" + candidate.MODULE_VERMAGIC
        for data in (module_elf(b"license=GPL\0", field + b"\0"), module_elf(field)):
            with self.subTest(data=data), self.assertRaisesRegex(ValueError, "modinfo|vermagic"):
                candidate.check_arm(data, "module.ko")

    def test_corrupt_section_table_and_payload_rejected(self):
        good = module_elf(b"vermagic=" + candidate.MODULE_VERMAGIC + b"\0")
        for offset, value in ((32, len(good)), (52 + 80 + 16, len(good) + 1)):
            data = bytearray(good)
            struct.pack_into("<I", data, offset, value)
            with self.subTest(offset=offset), self.assertRaisesRegex(ValueError, "section"):
                candidate.check_arm(bytes(data), "module.ko")


@unittest.skipUnless(all(shutil.which(x) for x in ("dtc", "fdtget", "fdtput")),
                     "device-tree host tools unavailable")
class AdaptationBootTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="z1-adaptation-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root / "candidate"
        self.rc = (
            "on fs\n    mount_all /fstab.z1\n"
            "    exec -- /z1stage_boot.sh --storage-only\n"
            "    wait_for_prop sys.z1.storage_ready 1\n"
            "on post-fs-data\n    exec -- /z1stage_boot.sh --skip-storage\n"
            "    wait_for_prop z1stage.status ready\n" + boot.AUDIO_LOAD +
            "    start z1_rootd\n    exec -- /z1_art_seed.sh\n")
        self.elf = bytearray(64)
        self.elf[:6] = b"\x7fELF\x01\x01"
        struct.pack_into("<H", self.elf, 16, 3)
        struct.pack_into("<H", self.elf, 18, 40)
        self.stage = b"#!/system/bin/sh\n# --storage-only --skip-storage sys.z1.storage_ready\n"
        self.files = {"init.z1.rc": self.rc.encode(), "hwcomposer.z1.so": bytes(self.elf),
                      "z1stage_boot.sh": self.stage, "sentinel": b"unchanged\0\xff"}
        self.body = b"".join(record(n, d) for n, d in self.files.items()) + record("TRAILER!!!", b"")
        zimage = bytearray(64)
        zimage[0x24:0x28] = bytes.fromhex("18286f01")
        struct.pack_into("<I", zimage, 0x2c, len(zimage))
        self.zimage = bytes(zimage)
        self.dtb = self.compile_dtb()
        self.write_base()
        (self.root / "zImage").write_bytes(self.zimage)
        (self.root / "board.dtb").write_bytes(self.dtb)
        (self.root / "new.so").write_bytes(bytes(self.elf) + b"new")
        self.manifest = self.root / "inputs.json"
        self.data = {"version": 1,
                     "base": self.identity("base.img"), "zimage": self.identity("zImage"),
                     "dtb": self.identity("board.dtb"),
                     "replacements": [{"name": "hwcomposer.z1.so",
                         "source": self.identity("new.so"),
                         "original_sha256": boot.sha(self.files["hwcomposer.z1.so"])}]}
        self.save()

    def compile_dtb(self, compatible="jbo,z1", bootargs="console=ttyS0,921600n8", start="84100000"):
        dts = f'''/dts-v1/;
        / {{ compatible = "{compatible}", "mediatek,mt6572";
             chosen {{ bootargs = "{bootargs}"; linux,initrd-start = <0x{start}>;
                      linux,initrd-end = <0x84200000>; }}; }};
        '''
        return subprocess.run(["dtc", "-I", "dts", "-O", "dtb"], input=dts.encode(),
                              check=True, capture_output=True).stdout

    def identity(self, name):
        return {"path": name, "sha256": boot.sha((self.root / name).read_bytes())}

    def write_base(self):
        header, kh, rh = bytearray(2048), bytearray(512), bytearray(512)
        header[:8] = b"ANDROID!"
        struct.pack_into("<I", header, 36, 2048)
        kh[:4] = rh[:4] = boot.MTK
        kh[8:14], rh[8:14] = b"KERNEL", b"ROOTFS"
        body = gzip.compress(self.body, mtime=0)
        struct.pack_into("<I", rh, 4, len(body))
        (self.root / "base.img").write_bytes(
            boot.make_image(header, kh, self.zimage, self.dtb, bytes(rh) + body))

    def save(self):
        self.manifest.write_text(json.dumps(self.data))

    def reject(self, message=".*"):
        before = (self.root / "base.img").read_bytes()
        with self.assertRaisesRegex((ValueError, OSError), message):
            candidate.build(self.manifest, self.output)
        self.assertFalse(os.path.lexists(self.output))
        self.assertEqual((self.root / "base.img").read_bytes(), before)

    def test_real_pack_reverse_identity_metadata_and_untouched_records(self):
        report = candidate.build(self.manifest, self.output)
        image = (self.output / "boot.img").read_bytes()
        self.assertEqual(report["boot_sha256"], boot.sha(image))
        _, _, zi, dt, rd = boot.split_boot(image)
        self.assertEqual(zi, self.zimage)
        body, records = boot.cpio(rd)
        files = boot.contents(body, records)
        self.assertEqual(files["hwcomposer.z1.so"], (self.root / "new.so").read_bytes())
        for name in ("init.z1.rc", "z1stage_boot.sh", "sentinel"):
            self.assertEqual(files[name], self.files[name])
        self.assertTrue(all(r["fields"][1] & 0o170000 == 0o100000 for r in records))
        self.assertEqual(int(report["initrd_end"], 16), 0x84100000 + len(rd) - 512)
        self.assertEqual(boot.dt_read(self.output / "appended.dtb", "/chosen", "linux,initrd-end"),
                         f"{0x84100000 + len(rd) - 512:x}")
        self.assertEqual(json.loads((self.output / "manifest.json").read_text()), report)
        for line in (self.output / "SHA256SUMS").read_text().splitlines():
            digest, name = line.split("  ")
            self.assertEqual(digest, boot.sha((self.output / name).read_bytes()))

    def test_all_input_hashes_and_original_hash_are_checked(self):
        original = copy.deepcopy(self.data)
        for key in ("base", "zimage", "dtb", "source", "old"):
            self.data = copy.deepcopy(original)
            if key in self.data:
                self.data[key]["sha256"] = "0" * 64
            elif key == "source":
                self.data["replacements"][0]["source"]["sha256"] = "0" * 64
            else:
                self.data["replacements"][0]["original_sha256"] = "0" * 64
            self.save()
            self.reject("SHA256 mismatch")

    def test_wrong_board_bootargs_or_initrd_address_rejected(self):
        for kwargs in ({"compatible": "other,board"}, {"bootargs": "console=tty0"},
                       {"start": "85000000"}):
            (self.root / "board.dtb").write_bytes(self.compile_dtb(**kwargs))
            self.data["dtb"] = self.identity("board.dtb")
            self.save()
            self.reject("boot contract")

    def test_disallowed_missing_or_duplicate_replacement(self):
        original = copy.deepcopy(self.data)
        for name in ("init.z1.rc", "../../x", "z1_cst836u.ko"):
            self.data = copy.deepcopy(original)
            self.data["replacements"][0]["name"] = name
            self.save()
            self.reject("cpio record")
        self.data = copy.deepcopy(original)
        self.data["replacements"] *= 2
        self.save()
        self.reject("duplicate cpio")

    def test_wrong_elf_and_zimage_headers_rejected(self):
        for target, key in (("new.so", "source"), ("zImage", "zimage"), ("board.dtb", "dtb")):
            old = (self.root / target).read_bytes()
            (self.root / target).write_bytes(b"bad file")
            if key == "source":
                self.data["replacements"][0][key] = self.identity(target)
            else:
                self.data[key] = self.identity(target)
            self.save()
            self.reject("ELF32|zImage|DTB")
            (self.root / target).write_bytes(old)
            if key == "source":
                self.data["replacements"][0][key] = self.identity(target)
            else:
                self.data[key] = self.identity(target)

    def test_wrong_elf_type_rejected(self):
        bad = bytearray(self.elf)
        struct.pack_into("<H", bad, 16, 2)
        (self.root / "new.so").write_bytes(bad)
        self.data["replacements"][0]["source"] = self.identity("new.so")
        self.save()
        self.reject("ELF32")

    def test_cpio_without_file_type_rejected(self):
        self.body = b"".join(record(n, d, 0o644 if n == "hwcomposer.z1.so" else 0o100644)
                             for n, d in self.files.items()) + record("TRAILER!!!", b"")
        self.write_base()
        self.data["base"] = self.identity("base.img")
        self.save()
        self.reject("replacement not regular")

    def test_missing_storage_gate_rejected(self):
        self.files["init.z1.rc"] = self.rc.replace("wait_for_prop sys.z1.storage_ready 1", "").encode()
        self.body = b"".join(record(n, d) for n, d in self.files.items()) + record("TRAILER!!!", b"")
        self.write_base()
        self.data["base"] = self.identity("base.img")
        self.save()
        self.reject("storage gate")

    def test_host_symlinks_fifo_and_existing_output_rejected(self):
        (self.root / "link.so").symlink_to(self.root / "new.so")
        os.mkfifo(self.root / "fifo")
        for name in ("link.so", "fifo"):
            self.data["replacements"][0]["source"]["path"] = name
            self.save()
            self.reject("symlink|regular host file")
        self.data["replacements"][0]["source"] = self.identity("new.so")
        self.save()
        self.output.mkdir()
        with self.assertRaisesRegex(ValueError, "new directory"):
            candidate.build(self.manifest, self.output)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_oversize_boot_not_published(self):
        huge = bytearray(boot.LIMIT)
        huge[:64] = self.zimage
        struct.pack_into("<I", huge, 0x2c, len(huge))
        (self.root / "zImage").write_bytes(huge)
        self.data["zimage"] = self.identity("zImage")
        self.save()
        self.reject("exceeds 6 MiB")
        self.assertFalse(list(self.root.glob(".z1-adaptation-*")))

    def test_duplicate_manifest_key_rejected(self):
        self.manifest.write_text('{"version":1,"version":1}')
        self.reject("duplicate manifest key")


if __name__ == "__main__":
    unittest.main()

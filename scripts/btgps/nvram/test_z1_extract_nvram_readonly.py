"""Tests for the Z1 raw NVRAM read-only extractor."""

import importlib.util
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = Path(__file__).with_name("z1_extract_nvram_readonly.py")
SPEC = importlib.util.spec_from_file_location("z1_nvram_readonly", SCRIPT)
nvram = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = nvram
SPEC.loader.exec_module(nvram)


def build_fixture() -> bytes:
    image = bytearray(nvram.IMAGE_SIZE)
    records = (
        (b"/data/nvram/APCFG/APRDEB/WIFI", bytes(range(256)) * 2),
        (b"/data/nvram/APCFG/APRDEB/BT_Addr", bytes(range(64))),
        (
            b"/data/nvram/md/NVRAM/NVD_IMEI/PRIVATE",
            b"must-not-be-extracted",
        ),
    )
    content = bytearray()
    for index, (path, payload) in enumerate(records):
        storage = payload + struct.pack(">H", nvram.checksum_record(payload))
        entry = nvram.HEADER_SIZE + index * nvram.ENTRY_SIZE
        path_offset = entry + nvram.PATH_OFFSET_IN_ENTRY
        struct.pack_into("<II", image, path_offset - 8, len(content), len(storage))
        image[path_offset:path_offset + len(path) + 1] = path + b"\0"
        content.extend(storage)

    struct.pack_into("<I", image, 4, len(content))
    struct.pack_into("<I", image, 0x0C, nvram.checksum_nvram(content))
    image[
        nvram.TOC_SIZE:nvram.TOC_SIZE + len(content)
    ] = content
    image[
        nvram.BACKUP_MAGIC_OFFSET:nvram.BACKUP_HEADER_OFFSET
    ] = nvram.BACKUP_MAGIC
    image[
        nvram.BACKUP_HEADER_OFFSET:
        nvram.BACKUP_HEADER_OFFSET + nvram.HEADER_BLOCK_SIZE
    ] = image[:nvram.HEADER_BLOCK_SIZE]
    return bytes(image)


class NvramReadonlyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="z1-nvram-readonly-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.image = self.root / "NVRAM"
        self.data = build_fixture()
        self.image.write_bytes(self.data)

    def test_parse_and_allowlist_extract_only_ap_records(self):
        records, digest = nvram.parse_image(self.data)
        payloads = nvram.approved_payloads(records)
        self.assertEqual(set(payloads), {"WIFI", "BT_Addr"})
        self.assertEqual(len(payloads["WIFI"]), 512)
        self.assertEqual(len(payloads["BT_Addr"]), 64)
        self.assertTrue(any("PRIVATE" in path for path in records))
        self.assertEqual(len(digest), 64)

        output = self.root / "restore"
        nvram.extract(output, payloads)
        self.assertEqual(
            (output / "APCFG/APRDEB/WIFI").read_bytes(),
            payloads["WIFI"],
        )
        self.assertEqual(
            (output / "APCFG/APRDEB/BT_Addr").read_bytes(),
            payloads["BT_Addr"],
        )
        extracted = [
            path.relative_to(output).as_posix()
            for path in output.rglob("*")
            if path.is_file()
        ]
        self.assertEqual(
            set(extracted),
            {"APCFG/APRDEB/WIFI", "APCFG/APRDEB/BT_Addr"},
        )
        self.assertFalse(any("PRIVATE" in path.name for path in output.rglob("*")))
        self.assertFalse((output / "RestoreFlag").exists())
        self.assertEqual(
            oct((output / "APCFG/APRDEB/WIFI").stat().st_mode & 0o777),
            "0o600",
        )

    def test_source_is_not_modified_and_report_excludes_values(self):
        before = self.image.read_bytes()
        data = nvram.read_readonly(self.image)
        records, digest = nvram.parse_image(data)
        payloads = nvram.approved_payloads(records)
        output = self.root / "restore"
        nvram.extract(output, payloads)
        report = nvram.make_report(data, records, digest, extracted=True)
        encoded = json.dumps(report)
        self.assertEqual(self.image.read_bytes(), before)
        self.assertEqual(report["status"], "EXTRACTED_READ_ONLY_SOURCE")
        self.assertFalse(report["restore_flag_created"])
        self.assertNotIn("PRIVATE", encoded)
        self.assertNotIn(payloads["BT_Addr"].hex(), encoded)
        self.assertNotIn(payloads["WIFI"].hex(), encoded)

    def test_whole_content_checksum_mismatch_is_rejected(self):
        corrupt = bytearray(self.data)
        corrupt[0x0C] ^= 1
        backup = nvram.BACKUP_HEADER_OFFSET
        corrupt[
            backup:backup + nvram.HEADER_BLOCK_SIZE
        ] = corrupt[:nvram.HEADER_BLOCK_SIZE]
        with self.assertRaisesRegex(ValueError, "Whole-NVRAM checksum"):
            nvram.parse_image(bytes(corrupt))

    def test_record_checksum_mismatch_is_rejected(self):
        corrupt = bytearray(self.data)
        corrupt[nvram.TOC_SIZE] ^= 1
        struct.pack_into(
            "<I",
            corrupt,
            0x0C,
            nvram.checksum_nvram(
                bytes(corrupt[
                    nvram.TOC_SIZE:
                    nvram.TOC_SIZE + struct.unpack_from("<I", corrupt, 4)[0]
                ])
            ),
        )
        backup = nvram.BACKUP_HEADER_OFFSET
        corrupt[
            backup:backup + nvram.HEADER_BLOCK_SIZE
        ] = corrupt[:nvram.HEADER_BLOCK_SIZE]
        with self.assertRaisesRegex(ValueError, "record checksum mismatch"):
            records, _ = nvram.parse_image(bytes(corrupt))
            nvram.approved_payloads(records)

    def test_backup_toc_mismatch_and_bad_size_are_rejected(self):
        corrupt = bytearray(self.data)
        corrupt[nvram.BACKUP_HEADER_OFFSET + 0x20] ^= 1
        with self.assertRaisesRegex(ValueError, "TOCs differ"):
            nvram.parse_image(bytes(corrupt))
        with self.assertRaisesRegex(ValueError, "Expected a"):
            nvram.parse_image(self.data[:-1])

    def test_existing_output_is_never_overwritten(self):
        records, _ = nvram.parse_image(self.data)
        payloads = nvram.approved_payloads(records)
        output = self.root / "restore"
        output.write_bytes(b"preserve")
        with self.assertRaisesRegex(ValueError, "already exists"):
            nvram.extract(output, payloads)
        self.assertEqual(output.read_bytes(), b"preserve")

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks are unavailable")
    def test_symlink_input_is_refused(self):
        link = self.root / "nvram-link"
        link.symlink_to(self.image)
        with self.assertRaises(OSError):
            nvram.read_readonly(link)


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""Host tests for the WiFi artifact gate; no device or large image required."""

import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


SPEC = importlib.util.spec_from_file_location(
    "wifi_identity", Path(__file__).with_name("z1_wifi_module_identity.py"))
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


def elf_module(name="wlan_gen2", vermagic=gate.EXPECTED_VERMAGIC,
               note=None, debug=False):
    """Small ARM ET_REL fixture with real section headers and GNU build ID."""
    if note is None:
        note = struct.pack("<III", 4, 4, 3) + b"GNU\0" + b"\x12\x34\x56\x78"
    bodies = [("", b""), (".shstrtab", b""),
              (".modinfo", ("name=" + name + "\0vermagic=" + vermagic + "\0").encode()),
              (".note.gnu.build-id", note)]
    if debug:
        bodies.append((".debug_info", b"debug"))
    names = b"\0"
    name_offsets = {"": 0}
    for name, _ in bodies[1:]:
        name_offsets[name] = len(names)
        names += name.encode() + b"\0"
    bodies[1] = (".shstrtab", names)
    image = bytearray(52)
    sections = []
    for index, (name, body) in enumerate(bodies):
        start = len(image)
        image.extend(body)
        sections.append((name_offsets[name], 3 if index == 1 else 1,
                         0, 0, start, len(body), 0, 0, 1, 0))
    shoff = len(image)
    for section in sections:
        image.extend(struct.pack("<10I", *section))
    struct.pack_into("<16sHHIIIIIHHHHHH", image, 0,
                     b"\x7fELF\x01\x01\x01" + b"\0" * 9,
                     1, 40, 1, 0, 0, shoff, 0, 52, 0, 0, 40, len(sections), 1)
    return bytes(image)


class FingerprintTests(unittest.TestCase):
    def matching(self, data):
        return mock.patch.object(gate, "EXPECTED_SHA256", hashlib.sha256(data).hexdigest())

    def test_identity_requires_digest_name_and_vermagic(self):
        data = elf_module()
        with self.matching(data):
            report = gate.fingerprint(data)
        self.assertTrue(report["matches_v35_module"])
        self.assertEqual(report["build_id"], "12345678")
        self.assertIsNone(report["srcversion"])
        self.assertFalse(report["has_dwarf"])

    def test_same_abi_and_name_without_known_digest_is_rejected(self):
        self.assertFalse(gate.fingerprint(elf_module())["matches_v35_module"])

    def test_name_and_abi_cannot_be_replaced_by_digest_only(self):
        for data in (elf_module(name="wlan_old"), elf_module(vermagic="3.4.67")):
            with self.subTest(data=hashlib.sha256(data).hexdigest()), self.matching(data):
                self.assertFalse(gate.fingerprint(data)["matches_v35_module"])

    def test_one_byte_corruption_does_not_pass(self):
        data = elf_module(debug=True)
        changed = data.replace(b"debug", b"debuG")
        with self.matching(data):
            self.assertFalse(gate.fingerprint(changed)["matches_v35_module"])

    def test_wrong_machine_or_executable_is_rejected(self):
        for offset, value in ((18, 3), (16, 2)):
            bad = bytearray(elf_module())
            struct.pack_into("<H", bad, offset, value)
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                gate.fingerprint(bad)

    def test_truncated_table_is_rejected(self):
        with self.assertRaises(ValueError):
            gate.fingerprint(elf_module()[:-1])

    def test_section_body_outside_file_is_rejected(self):
        bad = bytearray(elf_module())
        shoff = struct.unpack_from("<I", bad, 32)[0]
        struct.pack_into("<I", bad, shoff + 2 * 40 + 16, len(bad) + 1)
        with self.assertRaises(ValueError):
            gate.fingerprint(bad)

    def test_invalid_section_name_is_rejected(self):
        bad = bytearray(elf_module())
        shoff = struct.unpack_from("<I", bad, 32)[0]
        struct.pack_into("<I", bad, shoff + 2 * 40, len(bad) + 1)
        with self.assertRaises(ValueError):
            gate.fingerprint(bad)

    def test_truncated_build_id_is_rejected(self):
        for note in (b"tiny", struct.pack("<III", 4, 999, 3) + b"GNU\0"):
            with self.subTest(note=note), self.assertRaises(ValueError):
                gate.fingerprint(elf_module(note=note))


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.module = self.root / "known.ko"
        self.data = elf_module()
        self.module.write_bytes(self.data)

    def run_main(self, *args):
        stdout = io.StringIO()
        with mock.patch.object(sys, "argv", ["z1_wifi_module_identity.py", *map(str, args)]), \
                contextlib.redirect_stdout(stdout):
            code = gate.main()
        return code, json.loads(stdout.getvalue())

    def matching(self):
        return mock.patch.object(gate, "EXPECTED_SHA256", hashlib.sha256(self.data).hexdigest())

    def test_no_input_is_usage_error(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            self.run_main()
        self.assertEqual(error.exception.code, 2)

    def test_missing_module_is_reported(self):
        code, report = self.run_main("--module", self.root / "absent.ko")
        self.assertEqual(code, 1)
        self.assertIn("error", report["records"][0])
        self.assertEqual(report["artifact_identity"], "MISMATCH")

    def test_missing_image_never_invokes_debugfs(self):
        with mock.patch.object(gate.subprocess, "run") as run:
            code, report = self.run_main("--image", self.root / "absent.img")
        run.assert_not_called()
        self.assertEqual(code, 1)
        self.assertIn("not found", report["records"][0]["error"])

    def test_debugfs_is_read_only_and_member_is_fixed(self):
        image = self.root / "system.img"
        image.touch()
        result = subprocess.CompletedProcess([], 0, self.data, b"debugfs banner")
        with self.matching(), mock.patch.object(gate.subprocess, "run", return_value=result) as run:
            code, report = self.run_main("--image", image)
        self.assertEqual(code, 0)
        self.assertEqual(run.call_args.args[0],
                         ["debugfs", "-R", "cat " + gate.IMAGE_PATH, str(image)])
        self.assertTrue(run.call_args.kwargs["check"])
        self.assertIn("unverified", report["source_provenance"])

    def test_debugfs_timeout_is_a_mismatch(self):
        image = self.root / "system.img"
        image.touch()
        with mock.patch.object(gate.subprocess, "run", side_effect=subprocess.TimeoutExpired("debugfs", 120)):
            code, report = self.run_main("--image", image)
        self.assertEqual(code, 1)
        self.assertIn("error", report["records"][0])

    def test_debugfs_silent_missing_member_is_not_accepted(self):
        image = self.root / "system.img"
        image.touch()
        result = subprocess.CompletedProcess([], 0, b"", b"File not found")
        with mock.patch.object(gate.subprocess, "run", return_value=result):
            code, report = self.run_main("--image", image)
        self.assertEqual(code, 1)
        self.assertIn("ELF", report["records"][0]["error"])

    def test_mixed_inputs_fail_even_when_first_matches(self):
        with self.matching():
            code, report = self.run_main("--module", self.module,
                                         "--module", self.root / "absent.ko")
        self.assertEqual(code, 1)
        self.assertTrue(report["records"][0]["matches_v35_module"])
        self.assertFalse(report["records"][1]["matches_v35_module"])

    def test_prepatch_match_does_not_claim_source_provenance(self):
        with self.matching():
            code, report = self.run_main("--prepatch-rebuild", self.module)
        self.assertEqual(code, 0)
        self.assertEqual(report["records"][0]["kind"], "prepatch_rebuild")
        self.assertIn("unverified", report["source_provenance"])

    def test_output_is_exclusive_and_never_overwrites_evidence(self):
        output = self.root / "evidence.json"
        with self.matching():
            code, report = self.run_main("--module", self.module, "--output", output)
        before = output.read_bytes()
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(before), report)
        with self.assertRaises(FileExistsError):
            self.run_main("--module", self.module, "--output", output)
        self.assertEqual(output.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()

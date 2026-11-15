"""Host-only contracts for the fixed factory NVRAM dependency builder."""

import importlib.util
from dataclasses import replace
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import subprocess
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/z1_build_nvram_candidate.py"
SPEC = importlib.util.spec_from_file_location("z1_nvram_candidate", SCRIPT)
nvram = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(nvram)


class NvramDependencyTests(unittest.TestCase):
    def test_required_payload_set_is_the_daemon_closure(self):
        targets = {item.target for item in nvram.PAYLOADS}
        self.assertEqual(
            targets,
            {
                "/bin/nvram_daemon",
                "/vendor/lib/libnvram.so",
                "/vendor/lib/libcustom_nvram.so",
                "/vendor/lib/libnvram_sec.so",
                "/vendor/lib/libnvram_platform.so",
                "/vendor/lib/libfile_op.so",
                "/vendor/lib/libhwm.so",
                "/vendor/lib/libnvram_daemon_callback.so",
            },
        )
        self.assertNotIn("/bin/nvram_agent_binder", targets)
        self.assertNotIn("/vendor/lib/libnvramagentclient.so", targets)
        self.assertEqual(
            {
                item.runtime
                for item in nvram.PAYLOADS
                if item.target == "/bin/nvram_daemon"
            },
            {"/system/bin/nvram_daemon"},
        )

    def test_payload_hashes_are_pinned(self):
        for item in nvram.PAYLOADS:
            with self.subTest(payload=item.target):
                self.assertRegex(item.sha256, re.compile(r"^[0-9a-f]{64}$"))
                self.assertEqual(item.mode, 0o755 if item.target == "/bin/nvram_daemon" else 0o644)

    def test_init_contract_registers_only_a_disabled_daemon(self):
        data = nvram.INIT_RC.read_bytes()
        nvram.validate_init_rc(data)
        text = data.decode("ascii")
        self.assertIn("    disabled\n", text)
        self.assertIn("    oneshot\n", text)
        active = [line for line in text.splitlines() if line.strip()
                  and not line.lstrip().startswith("#")]
        self.assertEqual(len(active), 7)
        self.assertFalse(any(line.startswith("on ") for line in active))
        self.assertNotIn("mknod /dev/nvram", text)
        self.assertNotIn("setprop nvram_init", text)

    def test_init_contract_rejects_false_success_and_binder_drift(self):
        original = nvram.INIT_RC.read_bytes()
        cases = (
            original.replace(b"    oneshot\n", b""),
            original.replace(b"    disabled\n", b""),
            original.replace(b"    disabled\n", b"#    disabled\n"),
            original.replace(b"    user nvram\n", b"    user system\n"),
            original + b'service NvRAMAgent /system/bin/nvram_agent_binder\n',
            original + b'\non post-fs-data\n    start nvram_daemon\n',
            original + b'\non post-fs-data\n    setprop nvram_init Ready\n',
            original + b'\non post-fs-data\n    setprop ctl.start nvram_daemon\n',
            original + b'\non post-fs-data\n    rm /data/nvram/RestoreFlag\n',
            original + b'\non property:nvram_init=Ready\n    setprop sys.z1.nvram.ready 1\n',
            original + b'    disabled\n',
            original + b'    setenv LD_PRELOAD /data/injected.so\n',
            original + b'\\\n    start nvram_daemon\n',
            original + b'# continued comment\\\n',
            original.replace(b'    disabled\n', b'    disabled\r\n'),
            original + b'\x00',
        )
        for data in cases:
            with self.subTest(data=data[:80]), self.assertRaises(ValueError):
                nvram.validate_init_rc(data)
        self.assertNotEqual(original, b"")


@unittest.skipUnless(all(shutil.which(tool) for tool in ("mke2fs", "debugfs", "e2fsck")),
                     "ext4 host tools are unavailable")
class SmallExt4BuildTests(unittest.TestCase):
    """Exercise execute(), debugfs, e2fsck and publication with 8 MiB images.

    Only the fixture PAYLOADS hashes differ from production. All host image
    operations and both whole-image hash checks use the production code.
    """

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="z1-nvram-test-")
        cls.root = Path(cls.temp.name)
        cls.factory = cls.root / "factory.img"
        cls.base_image = cls.root / "base.img"
        cls.payloads = []
        cls.contents = {}
        for image in (cls.factory, cls.base_image):
            with image.open("wb") as stream:
                stream.truncate(8 * 1024 * 1024)
            subprocess.run(["mke2fs", "-q", "-F", "-t", "ext4", str(image)],
                           check=True, capture_output=True)
            for directory in ("/bin", "/lib", "/vendor", "/vendor/lib", "/etc", "/etc/init"):
                cls.debug(image, f"mkdir {directory}")
        for index, item in enumerate(nvram.PAYLOADS):
            data = bytearray(128)
            data[:6] = b"\x7fELF\x01\x01"
            struct.pack_into("<HH", data, 16, 3 if item.source.endswith(".so") else 2, 40)
            data[64:] = bytes([index + 1]) * 64
            data = bytes(data)
            cls.payloads.append(replace(item, sha256=nvram.sha_bytes(data)))
            cls.contents[item.target] = data
            source = cls.root / f"payload-{index}"
            source.write_bytes(data)
            cls.debug(cls.factory, f"write {source} {item.source}")
            # Include both existing-file replacement and absent-file insertion.
            if index % 2 == 0:
                cls.debug(cls.base_image, f"write {source} {item.target}")
        cls.factory_sha = nvram.base.sha(cls.factory)
        cls.base_sha = nvram.base.sha(cls.base_image)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    @staticmethod
    def debug(image, command):
        result = subprocess.run(["debugfs", "-w", "-R", command, str(image)],
                                check=True, capture_output=True, text=True)
        diagnostics = result.stderr.splitlines()[1:]
        if diagnostics:
            raise AssertionError(diagnostics)

    def setUp(self):
        self.output_dir = tempfile.TemporaryDirectory(prefix="result-", dir=self.root)
        self.addCleanup(self.output_dir.cleanup)
        self.output = Path(self.output_dir.name) / "system.img"
        self.payload_patch = mock.patch.object(nvram, "PAYLOADS", tuple(self.payloads))
        self.payload_patch.start()
        self.addCleanup(self.payload_patch.stop)

    def build(self):
        return nvram.execute(self.factory, self.base_image,
                             self.factory_sha, self.base_sha, self.output, True)

    def assert_failed_publication(self, *, foreign_image=None, foreign_report=None):
        if foreign_image is None:
            self.assertFalse(self.output.exists())
        else:
            self.assertEqual(self.output.read_bytes(), foreign_image)
        manifest = Path(str(self.output) + ".manifest.json")
        if foreign_report is None:
            self.assertFalse(manifest.exists())
        else:
            self.assertEqual(manifest.read_bytes(), foreign_report)
        marker = Path(str(self.output) + ".manifest.json.INVALID")
        self.assertEqual(json.loads(marker.read_text())["status"], "INVALID")
        self.assertEqual(nvram.base.sha(self.base_image), self.base_sha)
        self.assertEqual(nvram.base.sha(self.factory), self.factory_sha)

    def test_build_verifies_real_ext4_readback_and_metadata(self):
        report = self.build()
        self.assertEqual(report["sha256"], nvram.base.sha(self.output))
        self.assertEqual(report["status"], "HOST_VERIFIED_NOT_DEVICE_TESTED")
        self.assertFalse(report["nvram_service"]["automatic_start"])
        self.assertFalse(report["nvram_service"]["calibration_validated"])
        self.assertEqual(json.loads(Path(str(self.output) + ".manifest.json").read_text()), report)
        for suffix in (".INVALID", ".manifest.json.INVALID", ".manifest.json.partial"):
            self.assertFalse(Path(str(self.output) + suffix).exists())
        with nvram.base.regular_open(self.output) as image:
            nvram.base.fsck(image.fileno(), self.root)
            for item in self.payloads:
                label = b"u:object_r:vendor_file:s0" if item.target.startswith("/vendor/") \
                    else b"u:object_r:system_file:s0"
                nvram.verify_payload(image.fileno(), self.root, item,
                                     self.contents[item.target], label)
            rc = replace(self.payloads[0], target="/etc/init/z1_nvram.rc", mode=0o644,
                         sha256=nvram.sha_bytes(nvram.INIT_RC.read_bytes()))
            nvram.verify_payload(image.fileno(), self.root, rc, nvram.INIT_RC.read_bytes(),
                                 b"u:object_r:system_file:s0", check_elf=False)
        self.assertEqual(nvram.base.sha(self.base_image), self.base_sha)
        self.assertEqual(nvram.base.sha(self.factory), self.factory_sha)

    def test_each_publish_link_failure_withdraws_final_files(self):
        real_link = os.link
        for fail_call in (1, 2):
            with self.subTest(link=fail_call):
                self.output = Path(self.output_dir.name) / f"link-{fail_call}.img"
                calls = 0
                def failing_link(*args, **kwargs):
                    nonlocal calls
                    calls += 1
                    if calls == fail_call:
                        raise OSError("injected link failure")
                    return real_link(*args, **kwargs)
                with mock.patch.object(nvram.os, "link", side_effect=failing_link):
                    with self.assertRaisesRegex(OSError, "injected link"):
                        self.build()
                self.assert_failed_publication()

    def test_success_report_write_failure_cannot_publish(self):
        real_write = nvram.write_report_fd
        def failing_write(fd, report):
            if report["status"] == "HOST_VERIFIED_NOT_DEVICE_TESTED":
                raise OSError("injected report write failure")
            return real_write(fd, report)
        with mock.patch.object(nvram, "write_report_fd", side_effect=failing_write):
            with self.assertRaisesRegex(OSError, "report write"):
                self.build()
        self.assert_failed_publication()

    def test_image_report_and_each_directory_fsync_failure_withdraws(self):
        real_fsync = os.fsync
        for kind, occurrence in (("image", 1), ("image", 2), ("report", 1),
                                 ("directory", 1), ("directory", 2),
                                 ("directory", 3), ("directory", 4)):
            with self.subTest(kind=kind, occurrence=occurrence):
                self.output = Path(self.output_dir.name) / f"sync-{kind}-{occurrence}.img"
                calls = 0
                def failing_fsync(fd):
                    nonlocal calls
                    name = os.readlink(f"/proc/self/fd/{fd}")
                    matches = ((kind == "directory" and stat.S_ISDIR(os.fstat(fd).st_mode))
                               or (kind == "image" and name == str(self.output) + ".INVALID")
                               or (kind == "report" and name == str(self.output) + ".manifest.json.partial"))
                    if matches:
                        calls += 1
                        if calls == occurrence:
                            raise OSError("injected fsync failure")
                    return real_fsync(fd)
                with mock.patch.object(nvram.os, "fsync", side_effect=failing_fsync):
                    with self.assertRaisesRegex(OSError, "injected fsync"):
                        self.build()
                self.assertGreaterEqual(calls, occurrence)
                self.assert_failed_publication()

    def test_publication_does_not_withdraw_a_foreign_final_file(self):
        real_link = os.link
        def foreign_collision(source, destination, **kwargs):
            if destination == self.output.name:
                self.output.write_bytes(b"foreign file")
                raise FileExistsError("injected foreign output collision")
            return real_link(source, destination, **kwargs)
        with mock.patch.object(nvram.os, "link", side_effect=foreign_collision):
            with self.assertRaises(FileExistsError):
                self.build()
        self.assert_failed_publication(foreign_image=b"foreign file")

    def test_report_collision_preserves_foreign_report_and_withdraws_image(self):
        real_link = os.link
        manifest = Path(str(self.output) + ".manifest.json")
        def foreign_collision(source, destination, **kwargs):
            if destination == manifest.name:
                manifest.write_bytes(b"foreign report")
                raise FileExistsError("injected foreign report collision")
            return real_link(source, destination, **kwargs)
        with mock.patch.object(nvram.os, "link", side_effect=foreign_collision):
            with self.assertRaises(FileExistsError):
                self.build()
        self.assert_failed_publication(foreign_report=b"foreign report")

    def test_existing_invalid_marker_is_never_accepted_or_overwritten(self):
        marker = Path(str(self.output) + ".manifest.json.INVALID")
        marker.write_bytes(b"previous failure")
        with self.assertRaisesRegex(ValueError, "existing file"):
            self.build()
        self.assertEqual(marker.read_bytes(), b"previous failure")

    def test_whole_image_and_payload_hash_failures_never_publish(self):
        with self.assertRaisesRegex(ValueError, "Factory image SHA256 mismatch"):
            nvram.execute(self.factory, self.base_image, "0" * 64,
                          self.base_sha, self.output, True)
        self.assertFalse(self.output.exists())
        with mock.patch.object(nvram, "PAYLOADS", (replace(self.payloads[0], sha256="0" * 64),)):
            with self.assertRaisesRegex(ValueError, "Factory SHA256 mismatch"):
                self.build()
        self.assert_failed_publication()

class NvramRealImageTests(unittest.TestCase):
    @unittest.skipUnless(
        shutil.which("debugfs")
        and nvram.DEFAULT_BASE.is_file()
        and nvram.DEFAULT_FACTORY.is_file(),
        "local factory and candidate images are unavailable",
    )
    def test_real_inspection_is_read_only_and_hash_pinned(self):
        before_base = nvram.base.sha(nvram.DEFAULT_BASE)
        before_factory = nvram.base.sha(nvram.DEFAULT_FACTORY)
        report = nvram.execute(nvram.DEFAULT_FACTORY, nvram.DEFAULT_BASE)
        self.assertEqual(report["status"], "INSPECTED_ONLY_NOT_BUILT")
        self.assertEqual(report["base"]["sha256"], before_base)
        self.assertEqual(report["factory"]["sha256"], before_factory)
        self.assertEqual(len(report["required_payloads"]), len(nvram.PAYLOADS))
        self.assertEqual(nvram.base.sha(nvram.DEFAULT_BASE), before_base)
        self.assertEqual(nvram.base.sha(nvram.DEFAULT_FACTORY), before_factory)


if __name__ == "__main__":
    unittest.main(verbosity=2)

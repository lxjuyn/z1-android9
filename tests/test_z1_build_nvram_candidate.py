"""Host-only contracts for the fixed factory NVRAM dependency builder."""

import importlib.util
from pathlib import Path
import re
import shutil
import unittest


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

    def test_init_contract_starts_only_the_factory_daemon_after_data(self):
        data = nvram.INIT_RC.read_bytes()
        nvram.validate_init_rc(data)
        text = data.decode("ascii")
        self.assertLess(text.index("on post-fs-data"), text.index("start nvram_daemon"))
        self.assertIn("mkdir /data/nvram 2770 root nvram", text)
        self.assertIn("on property:nvram_init=Ready", text)
        self.assertNotIn("mknod /dev/nvram", text)
        self.assertNotIn("setprop nvram_init", text)

    def test_init_contract_rejects_false_success_and_binder_drift(self):
        original = nvram.INIT_RC.read_bytes()
        cases = (
            original.replace(b"    oneshot\n", b""),
            original.replace(b"    start nvram_daemon\n", b""),
            original.replace(b"    user nvram\n", b"    user system\n"),
            original + b'service NvRAMAgent /system/bin/nvram_agent_binder\n',
            original.replace(b"on property:nvram_init=Ready", b"on init"),
        )
        for data in cases:
            with self.subTest(data=data[:80]), self.assertRaises(ValueError):
                nvram.validate_init_rc(data)
        self.assertNotEqual(original, b"")

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

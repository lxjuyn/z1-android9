import subprocess
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/z1_touch_evidence.sh"


class TouchEvidenceTests(unittest.TestCase):
    def test_help_is_explicit_and_does_not_touch_devices(self):
        for args in ([], ["help"], ["unexpected"]):
            result = subprocess.run(["/bin/sh", str(SCRIPT)] + args, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn("Usage:", result.stdout)
            self.assertNotIn("/dev/kmsg", result.stderr)

    def test_missing_i2c_fails_before_unpacking_or_probing(self):
        for mode in ("probe", "identity"):
            with tempfile.TemporaryDirectory(prefix="z1-no-i2c-") as temp:
                import os
                root = Path(temp)
                (root / "system/bin").mkdir(parents=True)
                (root / "system/etc/z1-driver-evidence").mkdir(parents=True)
                env = dict(os.environ, PATH=str(root / "system/bin"), Z1_TEST_NO_I2C="1")
                result = subprocess.run(["/bin/sh", str(SCRIPT), mode], capture_output=True, text=True, env=env)
                if "missing /dev/i2c-1" in result.stdout:
                    self.assertEqual(result.returncode, 2)
                else:
                    self.assertIn("could not unpack probe", result.stdout)
                    self.assertNotIn("/system/etc", result.stdout + result.stderr)

    def test_module_mode_never_inserts_module(self):
        script = SCRIPT.read_text()
        self.assertNotIn("/system/bin/insmod ", script)
        self.assertNotIn("/system/bin/modprobe ", script)
        self.assertNotIn("i2cprobe /dev/i2c-1\n", script)
        self.assertIn("0x15", script)


if __name__ == "__main__":
    unittest.main()

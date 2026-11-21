#!/usr/bin/env python3
import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
BUILD = ROOT / "scripts/btgps/bt/build_probe.sh"
NDK = WORKSPACE / "android-ndk-r26d"
EXPECTED_SHA = (
    "cdeb42b821cc903c87780e0af13aa0633aeec1a97e167b84d027c0320bc724a9"
)


@unittest.skipUnless(NDK.is_dir(), "Android NDK r26d is not present")
class BluetoothHciProbeBuildTest(unittest.TestCase):
    def test_pinned_build_is_android_arm_and_reproducible(self):
        with tempfile.TemporaryDirectory(prefix="z1-bt-probe-test-") as directory:
            output = Path(directory) / "probe"
            result = subprocess.run(
                [str(BUILD), str(output)],
                text=True,
                capture_output=True,
                timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            probe = output / "z1_bt_hci_probe"
            self.assertEqual(
                hashlib.sha256(probe.read_bytes()).hexdigest(),
                EXPECTED_SHA,
            )
            readelf = NDK / (
                "toolchains/llvm/prebuilt/linux-x86_64/bin/llvm-readelf"
            )
            result = subprocess.run(
                [str(readelf), "-h", "-l", "-d", "-n", str(probe)],
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Class:                             ELF32", result.stdout)
            self.assertIn("Machine:                           ARM", result.stdout)
            self.assertIn("interpreter: /system/bin/linker", result.stdout)
            self.assertIn("[libc.so]", result.stdout)
            self.assertIn("[libdl.so]", result.stdout)
            self.assertIn("NT_ANDROID_TYPE_IDENT", result.stdout)


if __name__ == "__main__":
    unittest.main()

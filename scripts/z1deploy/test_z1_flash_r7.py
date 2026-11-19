#!/usr/bin/env python3
from pathlib import Path
import subprocess
import unittest


HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "z1_flash_r7.sh"


class FlashR7Test(unittest.TestCase):
    def test_script_is_syntax_valid_and_targets_only_boot_and_android(self):
        result = subprocess.run(
            ["bash", "-n", SCRIPT], text=True, capture_output=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        text = SCRIPT.read_text()
        self.assertIn('bootimg "$BOOT" "$BOOT_ROLLBACK" 8', text)
        self.assertIn('android "$SYSTEM" "$CHUNK_MB" "$TRIES"', text)
        self.assertNotIn('" cache ', text)
        self.assertNotIn('" usrdata ', text)
        self.assertIn("e2fsck -fn", text)
        self.assertIn("sha256sum -c SHA256SUMS", text)


if __name__ == "__main__":
    unittest.main()

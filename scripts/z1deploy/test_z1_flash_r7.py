#!/usr/bin/env python3
from pathlib import Path
import subprocess
import unittest


HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "z1_flash_r7.sh"
ROOT = HERE.parents[2]


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

    def test_mutually_exclusive_variants_are_rejected(self):
        result = subprocess.run(
            ["bash", SCRIPT, "--speaker-gpio", "--hwc-blank"],
            text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("select different boots", result.stderr)

    @unittest.skipUnless(
        (ROOT / "out/r7_speaker_gpio_candidate_20261119/boot.img").is_file()
        and (ROOT / "out/r7_speaker_gpio_candidate_20261119/system.img").is_file(),
        "speaker candidate artifacts are not present",
    )
    def test_speaker_variant_dry_run_passes(self):
        result = subprocess.run(
            ["bash", SCRIPT, "--speaker-gpio"],
            text=True,
            capture_output=True,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("R7 speaker GPIO preflight PASS", result.stdout)
        self.assertIn("GPIO140 active-high", result.stdout)
        self.assertIn("dry-run only", result.stdout)


if __name__ == "__main__":
    unittest.main()

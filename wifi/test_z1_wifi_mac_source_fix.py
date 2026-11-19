#!/usr/bin/env python3
import hashlib
import json
from pathlib import Path
import subprocess
import unittest


HERE = Path(__file__).resolve().parent
ROOT = HERE / "source_repro"
MANIFEST = ROOT / "manifest.json"
FIXED = ROOT / "wlan_gen2_mac_fixed.ko"
ORIGINAL_SHA = "2cf27905d382fb8ac7e6bd8e000c6db1417c4415b5718086562be11399580e9b"
FIXED_SHA = "143c5611267eb4c5e20135fffdd39db491753147ef295278c3a69330c1fbf80f"


class WiFiMacSourceFixTest(unittest.TestCase):
    def test_fixed_module_identity_and_source_pin(self):
        report = json.loads(MANIFEST.read_text())
        self.assertEqual(
            report["radio_source"]["commit"],
            "e9961becdc889266c3a8b5a488ff741159b6ad80",
        )
        self.assertEqual(
            report["radio_source"]["archive_sha256"],
            "af8bb14c434e2ef78d7367a28f2cd5a3a662adbece6b5c67d1e56f20e954613f",
        )
        self.assertEqual(
            report["kernel_source"]["commit"],
            "c6501138edf2ea6458c7c4370d2fc1033d54f50a",
        )
        self.assertEqual(
            report["kernel_source"]["config_sha256"],
            "8b0e88cd7a301d016a2f144710e044c9adb436a6ea8d97b33bf59dd7df9e7747",
        )
        self.assertTrue(report["validation"]["all_six_original_modules_byte_identical"])
        self.assertFalse(report["runtime_verified"])
        self.assertEqual(hashlib.sha256(FIXED.read_bytes()).hexdigest(), FIXED_SHA)
        self.assertEqual(FIXED.stat().st_size, 956328)
        self.assertNotEqual(ORIGINAL_SHA, FIXED_SHA)

    def test_source_patch_identity_and_three_mac_updates(self):
        report = json.loads(MANIFEST.read_text())
        patch_record = report["source_patch"]
        self.assertEqual(patch_record["file"], "source_compat.patch")

        patch = ROOT / patch_record["file"]
        self.assertEqual(
            hashlib.sha256(patch.read_bytes()).hexdigest(),
            patch_record["sha256"],
        )

        patch_lines = patch.read_text().splitlines()
        mac_updates = [
            line
            for line in patch_lines
            if line.startswith("+") and "eth_hw_addr_set(" in line
        ]
        self.assertEqual(len(mac_updates), 3)
        self.assertEqual(
            sum("wakeup_source_register(" in line for line in patch_lines),
            2,
        )
        self.assertEqual(
            sum("wakeup_source_unregister(" in line for line in patch_lines),
            2,
        )

    def test_fixed_module_metadata_and_mac_api(self):
        info = subprocess.run(
            ["modinfo", FIXED], text=True, capture_output=True, check=True
        ).stdout
        self.assertRegex(info, r"(?m)^name:\s+wlan_gen2$")
        self.assertIn(
            "vermagic:       7.0.0-rc7-z1+ SMP mod_unload ARMv7 p2v8",
            info,
        )
        self.assertIn(
            "depends:        cfg80211,mtk_stp_wmt_soc,mtk_wmt_wifi_soc",
            info,
        )
        relocations = subprocess.run(
            ["readelf", "-rW", FIXED],
            text=True,
            capture_output=True,
            check=True,
        ).stdout
        self.assertEqual(relocations.count(" dev_addr_mod"), 3)


if __name__ == "__main__":
    unittest.main()

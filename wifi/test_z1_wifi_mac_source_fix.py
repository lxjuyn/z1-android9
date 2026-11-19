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
        self.assertTrue(report["validation"]["all_six_original_modules_byte_identical"])
        self.assertFalse(report["runtime_verified"])
        self.assertEqual(hashlib.sha256(FIXED.read_bytes()).hexdigest(), FIXED_SHA)
        self.assertEqual(FIXED.stat().st_size, 956328)
        self.assertNotEqual(ORIGINAL_SHA, FIXED_SHA)

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

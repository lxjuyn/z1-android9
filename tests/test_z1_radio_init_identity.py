#!/usr/bin/env python3
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/z1_linux7_radio_init.sh"


class RadioInitIdentityTest(unittest.TestCase):
    def test_shell_syntax(self):
        result = subprocess.run(
            ["sh", "-n", SCRIPT], text=True, capture_output=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_wifi_identity_is_checked_before_module_insertion(self):
        text = SCRIPT.read_text()
        gate = text.index("module_sha256_matches wlan_gen2")
        wifi_insert = text.index('for module in $WIFI_MODULES; do', gate)
        self.assertLess(gate, wifi_insert)
        self.assertIn(
            "143c5611267eb4c5e20135fffdd39db491753147ef295278c3a69330c1fbf80f",
            text,
        )
        self.assertIn(
            "c9c62c26bc6027982f522ccba99ce0183ce976506e6432a311c682b895c7b07e",
            text,
        )
        self.assertIn("refusing to load an unpinned Wi-Fi driver", text)


if __name__ == "__main__":
    unittest.main()

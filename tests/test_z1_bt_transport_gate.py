#!/usr/bin/env python3
import os
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
BT = ROOT / "scripts/btgps/bt"
GATE = BT / "z1_bt_transport_gate.sh"
HOST = BT / "z1_bt_transport_host.sh"
DOC = BT / "HCI_PROBE.md"
PROBE = (
    ROOT.parent
    / "out/r7_bt_probe_writev_20261123_r2/z1_bt_hci_probe"
)


class BluetoothTransportGateTest(unittest.TestCase):
    def test_scripts_are_executable_and_parse(self):
        for script in (GATE, HOST):
            self.assertTrue(script.is_file())
            self.assertTrue(os.access(script, os.X_OK))
        subprocess.run(["sh", "-n", str(GATE)], check=True)
        subprocess.run(["bash", "-n", str(HOST)], check=True)

    def test_gate_rejects_unknown_mode_before_android_access(self):
        result = subprocess.run(
            ["sh", str(GATE), "invalid"],
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("usage:", result.stderr)

    @unittest.skipUnless(PROBE.is_file(), "pinned Android probe is missing")
    def test_host_dry_run_pushes_both_artifacts_and_uses_writev(self):
        result = subprocess.run(
            [str(HOST), "writev", "--dry-run"],
            text=True,
            capture_output=True,
            env={**os.environ, "Z1_BT_PROBE": str(PROBE)},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("z1_bt_hci_probe", result.stdout)
        self.assertIn("z1_bt_transport_gate.sh", result.stdout)
        self.assertIn("z1_bt_transport_gate.sh writev", result.stdout)
        self.assertIn("--rootcmd", result.stdout)

    def test_documentation_names_the_two_cold_boot_commands(self):
        text = DOC.read_text()
        self.assertIn("z1_bt_transport_host.sh write", text)
        self.assertIn("z1_bt_transport_host.sh writev", text)
        self.assertIn("separate cold boot", text)


if __name__ == "__main__":
    unittest.main()

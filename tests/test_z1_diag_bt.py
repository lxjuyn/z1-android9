#!/usr/bin/env python3
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/diag/z1_diag_bt.sh"


class BluetoothDiagnosticTest(unittest.TestCase):
    def test_shell_syntax(self):
        for shell in ("sh", "bash"):
            result = subprocess.run(
                [shell, "-n", SCRIPT], text=True, capture_output=True
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_records_combo_chip_id_without_printing_bt_records(self):
        text = SCRIPT.read_text()
        self.assertIn("getprop persist.mtk.wcn.combo.chipid", text)
        self.assertIn(
            'fact PRE_COMBO_CHIP_ID '
            '"$(getprop persist.mtk.wcn.combo.chipid 2>/dev/null)"',
            text,
        )
        self.assertIn(
            'fact PRE_BT_NVRAM_READY '
            '"$(getprop sys.z1.radio.bt_nvram_ready 2>/dev/null)"',
            text,
        )
        self.assertNotIn("/data/nvram/APCFG/APRDEB/BT_Addr", text)


if __name__ == "__main__":
    unittest.main()

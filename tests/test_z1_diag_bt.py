#!/usr/bin/env python3
from pathlib import Path
import importlib.util
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/diag/z1_diag_bt.sh"


class BluetoothDiagnosticTest(unittest.TestCase):
    def test_manager_evidence_is_required_for_framework_on(self):
        spec = importlib.util.spec_from_file_location(
            "z1_summary", ROOT / "scripts/diag/z1_diag_summarize.py"
        )
        summary = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(summary)
        base = {"PRE_STPBT_NODE": "yes", "TRIGGER_STACK_APPEARED": "1"}
        for extra, expected in (
            ({"TRIGGER_RESULT": "stack_started"}, summary.WARN),
            ({"TRIGGER_RESULT": "adapter_on_unconfirmed", "TRIGGER_ADAPTER_STATE": "OFF"}, summary.FAIL),
            ({"TRIGGER_RESULT": "adapter_on", "TRIGGER_ADAPTER_STATE": "ON"}, summary.OK),
        ):
            with self.subTest(extra=extra):
                sheet = summary.Sheet()
                summary.check_bt({**base, **extra}, sheet, None)
                self.assertEqual(sheet.rows[0]["status"], expected)

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

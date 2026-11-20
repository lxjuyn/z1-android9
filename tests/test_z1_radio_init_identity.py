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

    def test_factory_bluetooth_combo_chip_id_is_set_and_verified(self):
        text = SCRIPT.read_text()
        dt_gate = text.index("require_compatible / mediatek,mt6572")
        setprop = text.index(
            '/system/bin/setprop persist.mtk.wcn.combo.chipid "$combo_chip_id"'
        )
        verify = text.index(
            '[ "$observed_combo_chip_id" = "$combo_chip_id" ]'
        )
        bt_modules = text.index('BT_MODULES="mtk_btif_drv')
        self.assertLess(dt_gate, setprop)
        self.assertLess(setprop, verify)
        self.assertLess(verify, bt_modules)
        self.assertIn("combo_chip_id=0x6572", text)

    def test_bt_nvram_readiness_requires_shape_before_setting_factory_gate(self):
        text = SCRIPT.read_text()
        bt_record = text.index(
            'bt_nvram_record=/data/nvram/APCFG/APRDEB/BT_Addr'
        )
        wifi_record = text.index(
            'wifi_nvram_record=/data/nvram/APCFG/APRDEB/WIFI'
        )
        shape_gate = text.index('[ "$(wc -c < "$bt_nvram_record" 2>/dev/null)" = 64 ]')
        ready = text.index('/system/bin/setprop nvram_init Ready')
        state = text.index(
            '/system/bin/setprop sys.z1.radio.bt_nvram_ready "$bt_nvram_ready"'
        )
        self.assertLess(bt_record, shape_gate)
        self.assertLess(wifi_record, shape_gate)
        self.assertLess(shape_gate, ready)
        self.assertLess(ready, state)
        self.assertIn('| tr -d \' 0\\n\'', text)


if __name__ == "__main__":
    unittest.main()

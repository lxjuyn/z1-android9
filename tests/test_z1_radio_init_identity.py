#!/usr/bin/env python3
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/z1_linux7_radio_init.sh"
HAL_RC = ROOT / "rootdir/android.hardware.bluetooth@1.0-service.rc"
BUILDER = ROOT / "scripts/z1deploy/z1_build_r7_bt_r3_system.sh"


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

    def test_bt_only_reapplies_loaded_chip_identity_without_touching_wifi(self):
        text = SCRIPT.read_text()
        late = text[text.index("publish_bt_identity() {"):text.index("# Keep the initial publication")]
        late = late.replace("/system/bin/", "").replace("/data/nvram", "$CASE_DIR")
        fake = """
mode=--bt-identity-only
getprop() {
    case "$1" in
        ro.persistent_properties.ready) printf '%s\\n' "$PERSIST_READY" ;;
        persist.mtk.wcn.combo.chipid) cat "$CASE_DIR/chip" ;;
        nvram_init) cat "$CASE_DIR/nvram" ;;
    esac
}
setprop() {
    printf '%s=%s\\n' "$1" "$2" >> "$CASE_DIR/writes"
    case "$1" in
        persist.mtk.wcn.combo.chipid) printf '%s\\n' "$2" > "$CASE_DIR/chip" ;;
        nvram_init) printf '%s\\n' "$2" > "$CASE_DIR/nvram" ;;
    esac
}
chown() { :; }
chmod() { :; }
say() { :; }
"""
        with tempfile.TemporaryDirectory() as td:
            fixture = Path(td)
            aprdeb = fixture / 'APCFG/APRDEB'
            aprdeb.mkdir(parents=True)
            (fixture / 'chip').write_text('-1\n')
            (fixture / 'nvram').write_text('\n')
            # Match the factory runtime files: payload plus the retained MTK
            # two-byte storage checksum.
            (aprdeb / 'BT_Addr').write_bytes(bytes([1]) + bytes(63) + b'\xaa\xf1')
            (aprdeb / 'WIFI').write_bytes(bytes(512) + b'\xaa\xa4')
            for ready, expected_rc in [('false', 1), ('true', 0)]:
                result = subprocess.run(['sh', '-c', fake + late], capture_output=True,
                                        env=dict(os.environ, CASE_DIR=td, PERSIST_READY=ready))
                self.assertEqual(expected_rc, result.returncode, result.stderr)
                if ready == 'false':
                    self.assertFalse((fixture / 'writes').exists())
            self.assertEqual((fixture / 'writes').read_text().splitlines(), [
                'persist.mtk.wcn.combo.chipid=0x6572', 'nvram_init=Ready',
                'sys.z1.radio.bt_nvram_ready=1'])

    def test_bt_nvram_readiness_requires_shape_before_setting_factory_gate(self):
        text = SCRIPT.read_text()
        bt_record = text.index(
            'bt_nvram_record=/data/nvram/APCFG/APRDEB/BT_Addr'
        )
        wifi_record = text.index(
            'wifi_nvram_record=/data/nvram/APCFG/APRDEB/WIFI'
        )
        shape_gate = text.index('case "$bt_nvram_size" in')
        ready = text.index('/system/bin/setprop nvram_init Ready')
        state = text.index(
            '/system/bin/setprop sys.z1.radio.bt_nvram_ready "$bt_nvram_ready"'
        )
        self.assertLess(bt_record, shape_gate)
        self.assertLess(wifi_record, shape_gate)
        self.assertLess(shape_gate, ready)
        self.assertLess(ready, state)
        self.assertIn('| tr -d \' 0\\n\'', text)
        self.assertIn('64|66)', text)
        self.assertIn('512|514)', text)
        self.assertIn('/system/bin/chown 0:1050 "$nvram_dir"', text)
        self.assertIn('/system/bin/chown 0:1050 "$nvram_file"', text)

    def test_toybox_chown_uses_owner_group_syntax(self):
        text = SCRIPT.read_text()
        self.assertIn(
            "/system/bin/chown 1000:1000 /dev/stpwmt", text
        )
        self.assertIn(
            "/system/bin/chown 1002:1001 /dev/stpbt", text
        )
        self.assertNotIn(
            "/system/bin/chown 1000 1000 /dev/stpwmt", text
        )
        self.assertNotIn(
            "/system/bin/chown 1002 1001 /dev/stpbt", text
        )

    def test_factory_hal_has_supplementary_nvram_group(self):
        text = HAL_RC.read_text()
        self.assertIn(
            "service vendor.bluetooth-1-0 "
            "/vendor/bin/hw/android.hardware.bluetooth@1.0-service\n",
            text,
        )
        self.assertIn("    user bluetooth\n", text)
        self.assertIn("    group bluetooth nvram\n", text)
        self.assertNotIn("    group bluetooth\n", text)

    def test_builder_keeps_regular_file_type_bits_when_setting_mode(self):
        text = BUILDER.read_text()
        self.assertIn(
            'set_inode_field $target mode 0100$permission_bits',
            text,
        )
        self.assertNotIn('set_inode_field $target mode 0$mode', text)


if __name__ == "__main__":
    unittest.main()

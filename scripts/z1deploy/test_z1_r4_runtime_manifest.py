#!/usr/bin/env python3
"""Host-only tests for the read-only R4 runtime manifest gate."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "z1_r4_runtime_manifest.sh"
MANIFEST = HERE / "r4_runtime_manifest.json"


class R4RuntimeManifestTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(MANIFEST.read_text())

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="z1-r4-manifest-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "system/bin"
        self.bin.mkdir(parents=True)
        self.commands = self.root / "commands.log"
        for name in ("awk", "cat", "date", "grep", "sha256sum", "stat", "tr"):
            executable = shutil.which(name)
            if executable:
                os.symlink(executable, self.bin / name)
        self.write("proc/version", "Linux version 7.0.0-rc7-z1+ test\n")
        self.write("proc/modules", "\n".join(
            f"{name} 1 0 - Live 0x00000000" for name in self.manifest["loaded_modules"]
        ) + "\n")
        self.write("proc/bus/input/devices", 'N: Name="z1_cst836u"\n')
        self.write("proc/asound/cards", " 0 [mt6572mt6323]: mt6572-mt6323\n")
        self.write("sys/class/net/wlan0/operstate", "up\n")
        self.write_expected_files()
        self.write_getprop()

    def command(self, name, body):
        target = self.bin / name
        target.unlink(missing_ok=True)
        target.write_text(
            f'#!/bin/sh\nprintf "%s\\n" "{name} $*" >> "{self.commands}"\n{body}\n')
        target.chmod(0o755)

    def write(self, relative, content):
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    def write_expected_files(self):
        for item in self.manifest["runtime_files"]:
            relative = item["path"].lstrip("/")
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            # Hash output is mocked; these bytes exist only to exercise metadata checks.
            path.write_text("fixture:" + item["path"] + "\n")
            path.chmod(int(item["mode"], 8))
        # Preserve the exact semantic lines despite mocked file hashes.
        for item in self.manifest["content_checks"]:
            path = self.root / item["path"].lstrip("/")
            existing = path.read_text() if path.exists() else ""
            path.write_text(existing + item["line"] + "\n")

    def write_getprop(self):
        values = {
            "sys.boot_completed": "1",
            "sys.z1.storage_ready": "1",
            "z1stage.status": "ready",
            "sys.z1.radio.modules_ready": "1",
            "sys.z1.radio.wifi_modules_ready": "1",
            "z1stage.hwcomposer.sha": (
                "35176357f81284ffc11d56d2811cab52c77a0cdcfaf4678866e54bf00d579912"),
            "z1stage.hwcomposer.boot": (
                "35176357f81284ffc11d56d2811cab52c77a0cdcfaf4678866e54bf00d579912"),
            "z1stage.hwcomposer.source": "ramdisk",
            "init.svc.vendor.hwcomposer-2-1": "running",
        }
        cases = "\n".join(
            f' {key}) printf "%s\\n" "{value}" ;;' for key, value in values.items())
        self.command("getprop", f'case "$1" in{cases}\n *) exit 1 ;;\nesac')

    def write_tools(self, hash_overrides=None):
        hash_overrides = hash_overrides or {}
        hash_cases = "\n".join(
            f' {self.root / item["path"].lstrip("/")}) '
            f'printf "%s  %s\\n" '
            f'"{hash_overrides.get(item["path"], item["sha256"])}" "$1" ;;'
            for item in self.manifest["runtime_files"])
        modes = {
            str(self.root / item["path"].lstrip("/")): item["mode"]
            for item in self.manifest["runtime_files"]}
        mode_cases = "\n".join(
            f' {path}) printf "%s:0:0\\n" "{mode}" ;;'
            for path, mode in modes.items())
        self.command("sha256sum", f'case "$1" in{hash_cases}\n *) exit 1 ;;\nesac')
        self.command("stat", f'case "$3" in{mode_cases}\n *) exit 1 ;;\nesac')

    def prepare_probe(self):
        text = SCRIPT.read_text()
        text = re.sub(
            r'(?<![\w/])/(system|sbin|bin|proc|sys|data|vendor)(?=[/"\s:;])',
            lambda match: str(self.root) + match.group(), text)
        text = re.sub(
            r'(?<![\w/])/(hwcomposer\.z1\.so)',
            lambda match: str(self.root) + match.group(), text)
        text = text.replace(
            str(self.root) + "/sys/module/z1_cst836u/parameters/key_y_min",
            "/sys/module/z1_cst836u/parameters/key_y_min")
        probe = self.root / "probe.sh"
        probe.write_text(text)
        return probe

    def run_probe(self, *args):
        return subprocess.run(
            ["dash", str(self.prepare_probe()), *args],
            text=True, capture_output=True, timeout=10)

    def assert_clean_run(self, result):
        self.assertEqual(result.stderr, "")
        self.assertNotIn("mutation-command", result.stdout)
        if self.commands.exists():
            calls = self.commands.read_text().splitlines()
            allowed = ("awk ", "date ", "getprop ", "grep ", "sha256sum ", "stat ")
            self.assertTrue(all(line.startswith(allowed) for line in calls), calls)

    def test_manifest_hashes_match_package_readbacks(self):
        expected = {
            "/vendor/lib/hw/hwcomposer.z1.so":
                "35176357f81284ffc11d56d2811cab52c77a0cdcfaf4678866e54bf00d579912",
            "/system/usr/keylayout/mt6779-keypad.kl":
                "da29948bb71336ccc73f0b05333be610cf18feee3987fddddafd7d44d59c5f55",
            "/system/lib/modules/z1_cst836u.ko":
                "03b0f94c67fab10551ea0085beffff4fb02c87878a2034f184fd28bca8368745",
            "/system/lib/modules/snd-soc-mt6323.ko":
                "af3931113c5d96e0768e06ee4b30596a71b5c72fd06233f0ee3cf248388e8945",
            "/system/etc/init/z1_touch_navigation.rc":
                "32b84644e3eeb7bb99af897d1f104ce7518fc87beafe919d0b81de4960b56901",
            "/system/build.prop":
                "421b3173d9f8aa93be410868001c449088ccd4cee7441db64b60f8c7426514c8",
            "/system/lib/modules/z1-radio/mtk_stp_bt_soc.ko":
                "01fbd5706e9c74dc9759d3da60da5e3a5443c5c71c12c0dc1a1464495e45f63e",
            "/system/lib/modules/z1-radio/wlan_gen2.ko":
                "2cf27905d382fb8ac7e6bd8e000c6db1417c4415b5718086562be11399580e9b",
            "/vendor/lib/egl/libGLES_mesa.so":
                "bb0ac77c03e79648db5dc8a480ec478be566b78c6105e9f781fd22100b46f445",
            "/vendor/lib/libgallium_dri.so":
                "492f0b5e871868f55023351722a609da6b65e186fbf31366a28d7faf981232b9",
        }
        actual = {item["path"]: item["sha256"] for item in self.manifest["runtime_files"]}
        for path, digest in expected.items():
            self.assertEqual(actual[path], digest)
            self.assertRegex(digest, r"^[0-9a-f]{64}$")

    def test_complete_runtime_passes_and_never_writes(self):
        self.write_tools()
        files = [self.root / item["path"].lstrip("/")
                 for item in self.manifest["runtime_files"]]
        before = [(path.read_bytes(), path.stat().st_mtime_ns) for path in files]
        result = self.run_probe()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("read_only=1", result.stdout)
        self.assertIn("summary pass=40 fail=0 status=PASS", result.stdout)
        self.assert_clean_run(result)
        self.assertEqual(before, [(path.read_bytes(), path.stat().st_mtime_ns) for path in files])

    def test_missing_boot_completion_and_stale_hwc_source_fail(self):
        self.write_tools()
        self.command("getprop", '''
case "$1" in
 sys.z1.storage_ready|sys.z1.radio.modules_ready|sys.z1.radio.wifi_modules_ready) printf '1\\n' ;;
 z1stage.status) printf 'ready\\n' ;;
 z1stage.hwcomposer.source) printf 'active:pinned\\n' ;;
 z1stage.hwcomposer.sha|z1stage.hwcomposer.boot) printf '%s\\n' 35176357f81284ffc11d56d2811cab52c77a0cdcfaf4678866e54bf00d579912 ;;
 init.svc.vendor.hwcomposer-2-1) printf 'running\\n' ;;
 sys.boot_completed) printf '\\n' ;;
 *) exit 1 ;;
esac''')
        result = self.run_probe()
        self.assertEqual(result.returncode, 1)
        self.assertIn("check=sys.boot_completed status=FAIL", result.stdout)
        self.assertIn("check=hwcomposer-source status=PASS", result.stdout)
        self.assert_clean_run(result)

    def test_hash_metadata_and_hardware_checks_fail_closed(self):
        self.write_tools()
        target = self.root / "system/lib/modules/z1_cst836u.ko"
        target.unlink()
        self.write("system/lib/modules/not-substituted.ko", "wrong\n")
        target.symlink_to(self.root / "system/lib/modules/not-substituted.ko")
        self.write("proc/modules", "mt6572_mt6323 1 0 - Live 0x0\n")
        self.write("proc/bus/input/devices", 'N: Name="other-touch"\n')
        self.write("proc/asound/cards", "0 [other]: other\n")
        result = self.run_probe()
        self.assertEqual(result.returncode, 1)
        self.assertIn(
            f"check={self.root}/system/lib/modules/z1_cst836u.ko status=FAIL",
            result.stdout)
        self.assertIn("check=loaded-module:z1_cst836u status=FAIL", result.stdout)
        self.assertIn("check=touch-input-registered status=FAIL", result.stdout)
        self.assertIn("check=audio-card-present status=FAIL", result.stdout)
        self.assert_clean_run(result)

    def test_page_cache_corruption_fails_graphics_and_framework_checks(self):
        self.write_tools({
            "/vendor/lib/egl/libGLES_mesa.so": "0" * 64,
            "/vendor/lib/libgallium_dri.so": "f" * 64,
            "/system/framework/oat/arm/services.odex":
                "53521bef610b9e914598efcb684435d283b9391d064eedaa88a1867653bc9d1c",
        })
        result = self.run_probe()
        self.assertEqual(result.returncode, 1)
        self.assertIn(
            f"check={self.root}/vendor/lib/egl/libGLES_mesa.so status=FAIL",
            result.stdout)
        self.assertIn(
            f"check={self.root}/vendor/lib/libgallium_dri.so status=FAIL",
            result.stdout)
        self.assertIn(
            f"check={self.root}/system/framework/oat/arm/services.odex status=FAIL",
            result.stdout)
        self.assert_clean_run(result)

    def test_invalid_arguments_stop_before_sampling(self):
        for args in (("--bad",), ("extra", "more")):
            result = self.run_probe(*args)
            self.assertEqual(result.returncode, 2)
            self.assertNotIn("[r4manifest] BEGIN", result.stdout)
            self.assertFalse(self.commands.exists())

    def test_help_and_shell_syntax(self):
        self.assertIn("Usage:", self.run_probe("--help").stdout)
        for shell in ("dash", "bash"):
            result = subprocess.run(
                [shell, "-n", str(SCRIPT)], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_manifest_is_deterministic_json(self):
        raw = MANIFEST.read_text()
        self.assertEqual(json.dumps(json.loads(raw), indent=2) + "\n", raw)
        status = self.manifest["current_status"]
        self.assertEqual(status["status"], "POST_FLASH_RUNTIME_NOT_CAPTURED")
        self.assertFalse(status["accepted_post_flash_boot"])
        self.assertIn("22:46:01", status["reason"])
        self.assertIn("22:51:08", status["reason"])


if __name__ == "__main__":
    unittest.main()

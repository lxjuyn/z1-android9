#!/usr/bin/env python3
"""Host-only isolated fixtures; no board, sysfs writes or device I/O."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('inspect_power', HERE / 'inspect_power.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class PowerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='fixture_', dir=HERE / 'test_results')
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, name, value):
        p = self.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(value)
        return p

    def run_inspector(self, root=None):
        result = subprocess.run([sys.executable, str(HERE / 'inspect_power.py'), '--snapshot', str(root or self.root)], capture_output=True, text=True, timeout=3)
        self.assertEqual(result.stderr, '')
        return result.returncode, json.loads(result.stdout)

    def topic(self, report, topic):
        return next(f for f in report['findings'] if f['topic'] == topic)

    def test_empty_is_unknown(self):
        code, report = self.run_inspector()
        self.assertEqual(code, 4)
        self.assertEqual(report['readiness'], 'UNKNOWN')
        self.assertLessEqual(report['files_attempted'], 128)

    def test_z1diag_observed_blocks(self):
        self.put('sys/power/wake_lock', 'z1diag\n')
        code, report = self.run_inspector()
        self.assertEqual(code, 3)
        self.assertEqual(self.topic(report, 'wake_locks')['status'], 'BLOCKED')

    def test_other_lock_not_echoed(self):
        self.put('sys/power/wake_lock', 'private-marker\n')
        code, report = self.run_inspector()
        self.assertEqual(code, 3)
        self.assertNotIn('private-marker', json.dumps(report))

    def test_s2idle_not_deep(self):
        self.put('sys/power/state', 'freeze mem\n')
        self.put('sys/power/mem_sleep', '[s2idle]\n')
        code, report = self.run_inspector()
        self.assertEqual(code, 4)
        self.assertEqual(self.topic(report, 'deep_sleep')['status'], 'NOT_ADVERTISED')

    def test_deep_advertised_unproven(self):
        self.put('sys/power/mem_sleep', 's2idle [deep]\n')
        code, report = self.run_inspector()
        self.assertEqual(code, 4)
        self.assertFalse(report['hardware_tested'])
        self.assertEqual(self.topic(report, 'deep_sleep')['status'], 'ADVERTISED_UNTESTED')

    def test_config_does_not_disable_android_autosuspend(self):
        self.put('kernel.config', '# CONFIG_PM_AUTOSLEEP is not set\n')
        code, report = self.run_inspector()
        self.assertEqual(code, 4)
        self.assertIn('Android 9 libsuspend', self.topic(report, 'autosuspend')['detail'])

    def test_packed_policy_and_watchdog_contract(self):
        self.put('init.z1.rc', (HERE / 'sources' / 'packed_init.z1.rc').read_text())
        code, report = self.run_inspector()
        self.assertEqual(code, 4)
        self.assertEqual(self.topic(report, 'guard_policy')['status'], 'CONFIGURED_BLOCK')
        self.assertIn('timeout=40s', self.topic(report, 'watchdog_config')['detail'])

    def test_comments_not_active_policy(self):
        self.put('init.z1.rc', '# write /sys/power/wake_lock z1diag\n')
        code, report = self.run_inspector()
        self.assertEqual(self.topic(report, 'guard_policy')['status'], 'UNKNOWN')

    def test_rtc_alarm_present_is_not_wake_proof(self):
        self.put('sys/class/rtc/rtc0/name', 'mt6397-rtc\n')
        self.put('sys/class/rtc/rtc0/wakealarm', '\n')
        self.put('sys/class/rtc/rtc0/device/power/wakeup', 'enabled\n')
        code, report = self.run_inspector()
        self.assertEqual(code, 4)
        self.assertEqual(self.topic(report, 'rtc0_alarm')['status'], 'ALARM_INTERFACE_PRESENT')
        self.assertEqual(self.topic(report, 'rtc0_wake')['status'], 'UNKNOWN')

    def test_rtc_disabled_distinct(self):
        self.put('sys/class/rtc/rtc1/device/power/wakeup', 'disabled\n')
        code, report = self.run_inspector()
        self.assertEqual(self.topic(report, 'rtc1_wake')['status'], 'DISABLED')

    def test_irq_mask_failure_blocks(self):
        self.put('power_kernel.log', 'PMIC IRQ entered 64 times with no status bit set; masking the parent IRQ\n')
        code, report = self.run_inspector()
        self.assertEqual(code, 3)
        self.assertEqual(self.topic(report, 'pmic_irq')['status'], 'BLOCKED')

    def test_pmic_fallback_failure_blocks(self):
        self.put('power_kernel.log', 'mt6397-pmic: failed to register irq=37; err: -22 (continuing: keep irq_domain, PMIC cells keep probing)\n')
        self.assertEqual(self.run_inspector()[0], 3)

    def test_unrelated_irq_error_not_pmic_failure(self):
        self.put('power_kernel.log', 'unrelated-device: Failed to request IRQ: 8\nCan not find pin\n')
        code, report = self.run_inspector()
        self.assertEqual(code, 4)
        self.assertEqual(self.topic(report, 'pmic_irq')['status'], 'UNKNOWN')

    def test_registered_key_not_end_to_end(self):
        self.put('proc/bus/input/devices', 'N: Name="mtk-pmic-keys"\n')
        self.put('selected_pmic_keylayout.kl', 'key 116 POWER\n')
        code, report = self.run_inspector()
        self.assertEqual(code, 4)
        self.assertEqual(self.topic(report, 'power_key_device')['status'], 'REGISTERED_UNTESTED')

    def test_active_watchdog_never_certified(self):
        self.put('sys/class/watchdog/watchdog0/state', 'active\n')
        self.put('sys/class/watchdog/watchdog0/timeout', '31\n')
        self.put('sys/class/watchdog/watchdog0/nowayout', '0\n')
        code, report = self.run_inspector()
        self.assertEqual(code, 4)
        self.assertEqual(self.topic(report, 'watchdog_runtime')['status'], 'ACTIVE_HAZARD')

    def test_live_root_rejected(self):
        for root in ('/', '/sys', '/proc', '/dev'):
            self.assertEqual(self.run_inspector(root)[0], 2)

    def test_symlink_file_rejected(self):
        (self.root / 'kernel.config').symlink_to('/dev/null')
        self.assertEqual(self.run_inspector()[0], 2)

    def test_symlink_directory_rejected(self):
        (self.root / 'sys').symlink_to('/sys')
        self.assertEqual(self.run_inspector()[0], 2)

    def test_root_symlink_rejected(self):
        (self.root / 'link').symlink_to(self.root, target_is_directory=True)
        self.assertEqual(self.run_inspector(self.root / 'link')[0], 2)

    def test_fifo_rejected_without_wait(self):
        os.mkfifo(self.root / 'kernel.config')
        self.assertEqual(self.run_inspector()[0], 2)

    def test_oversize_rejected(self):
        self.put('kernel.config', 'x' * 65537)
        self.assertEqual(self.run_inspector()[0], 2)

    def test_binary_rejected(self):
        self.put('kernel.config', 'x\x00')
        self.assertEqual(self.run_inspector()[0], 2)

    def test_invalid_modes_rejected(self):
        self.put('sys/power/mem_sleep', '[s2idle] [deep]')
        self.assertEqual(self.run_inspector()[0], 2)

    def test_invalid_numeric_rejected(self):
        self.put('sys/class/watchdog/watchdog0/timeout', 'not-a-time')
        self.assertEqual(self.run_inspector()[0], 2)

    def test_missing_wakeup_count_never_read(self):
        p = self.put('sys/power/state', 'freeze mem\n').parent / 'wakeup_count'
        os.mkfifo(p)
        self.assertEqual(self.run_inspector()[0], 4)

    def test_no_data_mutation(self):
        p = self.put('sys/power/wake_lock', 'z1diag\n')
        before = (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
        self.run_inspector()
        self.assertEqual(before, (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns))

    def test_file_budget_enforced(self):
        snap = mod.Snapshot(str(self.root))
        try:
            snap.reads = mod.MAX_FILES
            with self.assertRaises(ValueError):
                snap.read('kernel.config')
        finally:
            snap.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)

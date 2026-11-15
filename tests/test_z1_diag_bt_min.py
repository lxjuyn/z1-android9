#!/usr/bin/env python3
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/diag/z1_diag_bt_min.sh'


class BluetoothMinimalProbeTest(unittest.TestCase):
    def setUp(self):
        self.text = SCRIPT.read_text()

    def test_is_posix_shell_and_syntax(self):
        self.assertTrue(self.text.startswith('#!/system/bin/sh'))
        self.assertNotIn('set bluetooth', self.text.lower())
        self.assertNotIn('svc bluetooth', self.text)

    def test_only_bounded_read_commands_are_used(self):
        self.assertIn('head -n 60', self.text)
        self.assertIn('tail -n 60', self.text)
        for forbidden in ('insmod', 'rmmod', 'settings put', 'echo 1 >', 'cat /data/local/tmp/z1stage/out.txt'):
            self.assertNotIn(forbidden, self.text)

    def test_expected_transport_and_hci_evidence(self):
        for needle in ('/dev/stpbt', '/dev/stpwmt', '/proc/modules', '/sys/class/bluetooth', 'hciconfig', 'init.svc.bluetooth', 'bluetooth_on', 'wmt'):
            self.assertIn(needle, self.text)

    def test_dmesg_filter_is_narrow(self):
        match = re.search(r"dmesg .*?grep -iE '([^']+)'", self.text, re.S)
        self.assertIsNotNone(match)
        pattern = match.group(1)
        for needle in ('btif', 'stpbt', 'hci', 'wmt'):
            self.assertIn(needle, pattern)


if __name__ == '__main__':
    unittest.main()

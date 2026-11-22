"""Crash-trigger checks for the Android 9 ACM capture helper."""
import importlib.util
from pathlib import Path
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "scripts/z1_capture_android9_acm.py"
SPEC = importlib.util.spec_from_file_location("z1_capture_android9_acm", SOURCE)
CAPTURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CAPTURE)


class CrashTriggerTests(unittest.TestCase):
    def test_system_server_signal_triggers_export(self):
        chunk = (
            b"[z1logcat]= init: Service 'system_server' (pid 1547) "
            b"received signal 11\n"
        )
        self.assertEqual(CAPTURE.find_crash_trigger(b"", chunk), "system_server")

    def test_zygote_signal_split_across_reads_triggers_export(self):
        previous = b"[z1logcat]= init: Service 'zygote' (pid 941)"
        chunk = b" received signal 9\n"
        self.assertEqual(CAPTURE.find_crash_trigger(previous, chunk), "zygote")

    def test_fatal_signal_with_process_identity_triggers_export(self):
        chunk = (
            b"[z1logcat]= Fatal signal 11 in tid 1604, "
            b"pid 1547 (system_server)\n"
        )
        self.assertEqual(CAPTURE.find_crash_trigger(b"", chunk), "fatal")

    def test_unrelated_service_restart_does_not_trigger(self):
        chunk = b"[z1logcat]= init: Service 'netd' (pid 155) received signal 11\n"
        self.assertIsNone(CAPTURE.find_crash_trigger(b"", chunk))


if __name__ == "__main__":
    unittest.main()

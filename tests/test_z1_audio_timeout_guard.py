"""Regression tests for the Z1 audio service-dump timeout guard."""

from pathlib import Path
import re
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = (
    ROOT / "scripts/diag/z1_diag_audio.sh",
    ROOT / "scripts/diag/z1_diag_all.sh",
)
UNSAFE_DUMP = re.compile(
    r"\bdumpsys\s+media\.audio_(?:flinger|policy)\b"
)


def active_text(path):
    return "\n".join(
        line for line in path.read_text().splitlines()
        if not line.lstrip().startswith("#")
    )


class AudioTimeoutGuardTests(unittest.TestCase):
    def test_live_diagnostics_do_not_call_audio_binder_dumps(self):
        for script in SCRIPTS:
            with self.subTest(script=script.name):
                self.assertIsNone(UNSAFE_DUMP.search(active_text(script)))

    def test_live_diagnostics_query_service_registration(self):
        for script in SCRIPTS:
            text = active_text(script)
            with self.subTest(script=script.name):
                self.assertIn("service check media.audio_flinger", text)
                self.assertIn("service check media.audio_policy", text)

    def test_diagnostics_are_valid_posix_shell(self):
        shells = [
            shell for shell in ("sh", "dash", "bash")
            if shutil.which(shell)
        ]
        self.assertTrue(shells)
        for shell in shells:
            for script in SCRIPTS:
                with self.subTest(shell=shell, script=script.name):
                    result = subprocess.run(
                        [shell, "-n", str(script)],
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)

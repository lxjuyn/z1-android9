#!/usr/bin/env python3
from pathlib import Path
import subprocess
import unittest


HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "z1_audio_route.sh"


class AudioRouteTest(unittest.TestCase):
    def test_tinymix_values_match_android_9_types(self):
        text = SCRIPT.read_text()
        for control in (
            "Speaker Route Switch",
            "Speaker Switch",
            "Headphone Switch",
            "MIC Route Switch",
        ):
            self.assertIn(f'set_one "{control}" 1', text)
            self.assertNotIn(f'set_one "{control}" On', text)
        self.assertIn('set_one "AWB Source" MIC', text)
        self.assertIn('set_one "MIC Input Mux" MIC1', text)
        self.assertIn('[ "$failures" -eq 0 ]', text)

    def test_shell_syntax(self):
        result = subprocess.run(
            ["sh", "-n", SCRIPT], text=True, capture_output=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()

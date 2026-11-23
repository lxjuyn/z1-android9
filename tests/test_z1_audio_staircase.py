"""Static contract tests for the reset-isolating audio staircase."""

from pathlib import Path
import stat
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/diag/z1_audio_staircase.sh"


class AudioStaircaseTests(unittest.TestCase):
    def test_script_is_executable_and_valid_shell(self):
        mode = SCRIPT.stat().st_mode
        self.assertTrue(mode & stat.S_IXUSR)
        result = subprocess.run(
            ["sh", "-n", str(SCRIPT)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_stages_are_ordered_and_playback_has_no_concurrent_probe(self):
        text = SCRIPT.read_text()
        markers = [
            "STEP_00_START",
            "STEP_01_MIXER_SAVED",
            "STEP_02_MIXER_READ_BEGIN",
            "set_control 03_HEADPHONE_OFF",
            "set_control 04_SPEAKER_ROUTE_ON",
            "set_control 05_SPEAKER_SWITCH_ON",
            "set_control 06_SPEAKER_VOLUME",
            "STEP_07_TINYPCINFO_BEGIN",
            "STEP_08_TINYPLAY_BEFORE",
            "STEP_09_TINYPLAY_STARTED",
            "STEP_10_TINYPLAY_PCM",
            "STEP_11_TINYPLAY_DONE",
            "STEP_12_DONE",
        ]
        positions = [text.index(marker) for marker in markers]
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn("/sys/kernel/debug", text)
        self.assertNotIn("regread", text)


if __name__ == "__main__":
    unittest.main()

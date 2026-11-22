"""Host-side tests for the acoustic speaker measurement classifier."""

import importlib.util
import json
import math
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import wave


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/diag/z1_speaker_measure.py"
SPEC = importlib.util.spec_from_file_location("z1_speaker_measure", SOURCE)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def write_pcm16_wav(path, channels, sample_rate=48000):
    frames = max(len(channel) for channel in channels)
    payload = bytearray()
    for frame in range(frames):
        for channel in channels:
            value = channel[frame] if frame < len(channel) else 0
            payload.extend(struct.pack("<h", value))
    with wave.open(str(path), "wb") as output:
        output.setnchannels(len(channels))
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(payload)


def silence(seconds, sample_rate=48000):
    return [0] * round(seconds * sample_rate)


def tone(frequency, seconds, amplitude, sample_rate=48000):
    return [
        round(amplitude * math.sin(2 * math.pi * frequency * index / sample_rate))
        for index in range(round(seconds * sample_rate))
    ]


class SpeakerMeasureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="z1-speaker-measure-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def analyze(self, path, frequency, **kwargs):
        return MODULE.analyze_wav(
            path,
            (frequency,),
            min_amplitude=0.25,
            min_snr_db=12.0,
            min_run_ms=500,
            baseline_ms=250,
            **kwargs,
        )

    def test_sustained_tone_after_quiet_baseline_passes(self):
        path = self.root / "tone.wav"
        samples = silence(1.0) + tone(500, 1.0, 2000) + silence(1.0)
        write_pcm16_wav(path, [samples])
        report = self.analyze(path, 500)
        self.assertEqual(report["result"], "PASS")
        best = report["frequencies"][0]["best_channel"]
        self.assertGreaterEqual(best["run_duration_ms"], 500)
        self.assertGreater(best["snr_db"], 12)

    def test_short_click_is_not_a_sustained_tone(self):
        path = self.root / "click.wav"
        samples = silence(1.0) + tone(500, 0.12, 2000) + silence(1.0)
        write_pcm16_wav(path, [samples])
        report = self.analyze(path, 500)
        self.assertEqual(report["result"], "FAIL")

    def test_wrong_frequency_fails(self):
        path = self.root / "wrong.wav"
        samples = silence(0.75) + tone(660, 1.0, 2000) + silence(0.75)
        write_pcm16_wav(path, [samples])
        report = self.analyze(path, 500)
        self.assertEqual(report["result"], "FAIL")

    def test_stereo_channel_tone_is_detected(self):
        path = self.root / "stereo.wav"
        left = silence(0.75) + silence(1.0) + silence(0.75)
        right = silence(0.75) + tone(660, 1.0, 2000) + silence(0.75)
        write_pcm16_wav(path, [left, right])
        report = self.analyze(path, 660)
        self.assertEqual(report["result"], "PASS")
        self.assertEqual(report["frequencies"][0]["best_channel"]["channel"], 1)

    def test_continuous_tone_requires_onset_by_default(self):
        path = self.root / "continuous.wav"
        write_pcm16_wav(path, [tone(500, 2.0, 2000)])
        report = self.analyze(path, 500)
        self.assertFalse(report["frequencies"][0]["passed"])
        self.assertFalse(
            report["frequencies"][0]["best_channel"]["onset_after_quiet_baseline"]
        )

    def test_existing_tone_and_later_click_do_not_combine_to_pass(self):
        path = self.root / "false-onset.wav"
        write_pcm16_wav(path, [
            tone(500, 1.0, 2000) + silence(0.8) + tone(500, 0.08, 2000) + silence(0.8)
        ])
        self.assertEqual(self.analyze(path, 500)["result"], "FAIL")

    def test_run_duration_does_not_include_a_nonexistent_last_hop(self):
        path = self.root / "stationary.wav"
        write_pcm16_wav(path, [tone(500, 0.45, 2000) + silence(1.0)])
        report = MODULE.analyze_wav(path, (500,), min_amplitude=1500, require_onset=False)
        self.assertEqual(report["result"], "FAIL")
        self.assertEqual(report["frequencies"][0]["best_channel"]["run_duration_ms"], 450)

    def test_serial_transport_completion_is_not_board_command_success(self):
        self.assertTrue(MODULE._serial_command_succeeded("data\r\n###RC=0 ###DONE\r\n"))
        for reply in ("###DONE", "###RC=124 ###DONE\n", "###RC=1 ###DONE\n###RC=0 ###DONE\n"):
            with self.subTest(reply=reply):
                self.assertFalse(MODULE._serial_command_succeeded(reply))

    def test_cli_writes_json_report_and_uses_exit_status(self):
        path = self.root / "tone.wav"
        report_path = self.root / "result.json"
        write_pcm16_wav(
            path,
            [silence(0.75) + tone(500, 1.0, 2000) + silence(0.75)],
        )
        result = subprocess.run(
            [
                sys.executable,
                str(SOURCE),
                "analyze",
                str(path),
                "--frequency",
                "500",
                "--report",
                str(report_path),
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(report_path.read_text())
        self.assertEqual(report["schema"], MODULE.SCHEMA)
        self.assertEqual(report["result"], "PASS")


if __name__ == "__main__":
    unittest.main()

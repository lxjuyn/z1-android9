"""Run the installed loader against temporary module, sysfs and ALSA fixtures."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "scripts/r5_20261028/load_audio.sh"
MODULES = ["soundcore", "snd", "snd-timer", "snd-pcm", "snd-soc-core",
           "snd-soc-mtk-common", "snd-soc-mt6323", "snd-soc-simple-amplifier",
           "mt6323-accdet", "mt6572-afe-pcm", "mt6572-mt6323"]


class AudioLoaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="z1-audio-loader-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for directory in ("system/lib/modules", "sys/module", "proc/asound", "dev", "bin"):
            (self.root / directory).mkdir(parents=True, exist_ok=True)
        for name in MODULES:
            (self.root / "system/lib/modules" / (name + ".ko")).touch()
        (self.root / "proc/asound/cards").write_text(" 0 [mt6572mt6323  ]: mt6572-mt6323 - z1\n")
        (self.root / "proc/modules").write_text("snd 8192 0 - Live 0x0\n")
        mock = self.root / "bin/insmod"
        mock.write_text('#!/bin/sh\nprintf "%s\\n" "${1##*/}" >> "$CALLS"\n'
                        '[ "${1##*/}" != "$FAIL_MODULE" ]\n')
        mock.chmod(0o755)
        text = SOURCE.read_text()
        for prefix in ("/system/lib/modules", "/sys/module", "/proc/", "/dev/kmsg"):
            text = text.replace(prefix, str(self.root) + prefix)
        self.script = self.root / "loader.sh"
        self.script.write_text(text)
        self.env = dict(os.environ, PATH=str(self.root / "bin") + os.pathsep + os.environ["PATH"],
                        CALLS=str(self.root / "calls"), FAIL_MODULE="")

    def run_loader(self):
        return subprocess.run(["dash", str(self.script)], env=self.env, capture_output=True, text=True)

    def calls(self):
        return (self.root / "calls").read_text().splitlines() if (self.root / "calls").exists() else []

    def test_complete_stack_and_dependency_order(self):
        result = self.run_loader()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), [name + ".ko" for name in MODULES])
        self.assertNotIn("z1_charger.ko", self.calls())

    def test_missing_module_is_failure(self):
        (self.root / "system/lib/modules/snd-soc-mt6323.ko").unlink()
        self.assertNotEqual(self.run_loader().returncode, 0)
        self.assertNotIn("snd-soc-mt6323.ko", self.calls())

    def test_insmod_failure_is_reported_even_when_a_card_exists(self):
        self.env["FAIL_MODULE"] = "mt6572-mt6323.ko"
        self.assertNotEqual(self.run_loader().returncode, 0)
        self.assertIn("mt6572-mt6323.ko", self.calls())

    def test_empty_alsa_cards_is_failure(self):
        (self.root / "proc/asound/cards").write_text("--- no soundcards ---\n")
        self.assertNotEqual(self.run_loader().returncode, 0)

    def test_absent_alsa_cards_is_failure(self):
        (self.root / "proc/asound/cards").unlink()
        self.assertNotEqual(self.run_loader().returncode, 0)

    def test_already_loaded_modules_are_not_reinserted(self):
        for name in MODULES:
            (self.root / "sys/module" / name.replace("-", "_")).mkdir()
        self.assertEqual(self.run_loader().returncode, 0)
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()

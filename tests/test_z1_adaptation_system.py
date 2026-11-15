"""Real ext4 round trips for the additional fixed adaptation paths."""
import copy
import importlib.util
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import z1_build_adaptation_system as adaptation

spec = importlib.util.spec_from_file_location("driver_tests", Path(__file__).with_name(
    "test_z1_driver_candidate.py"))
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)


class AdaptationSystemTests(unittest.TestCase):
    setUp = fixtures.CandidateTests.setUp
    dbg = fixtures.CandidateTests.dbg
    save = fixtures.CandidateTests.save

    def add_target(self, target, payload):
        for directory in ("/lib", "/lib/modules", "/vendor/etc"):
            self.dbg("mkdir " + directory, write=True)
        for command in (f"write old {target}", f"sif {target} mode 0100640",
                        f"sif {target} uid 1234", f"sif {target} gid 5678",
                        f"ea_set -f label {target} security.selinux"):
            self.dbg(command, write=True)
        self.payload.write_bytes(payload)
        self.data["base"]["sha256"] = adaptation.base.sha(self.base)
        entry = self.data["replacements"][0]
        entry.update(path=target, sha256=adaptation.base.sha(self.payload))
        self.save()

    def test_loader_real_roundtrip_and_default_builder_still_rejects(self):
        data = b"#!/system/bin/sh\nexit 0\n"
        self.add_target(adaptation.LOADER, data)
        with self.assertRaisesRegex(ValueError, "Disallowed"):
            adaptation.base.execute(self.manifest, self.output, True)
        report = adaptation.execute(self.manifest, self.output, True)
        self.assertEqual(report["status"], "HOST_VERIFIED_NOT_DEVICE_TESTED")
        self.dbg("dump " + adaptation.LOADER + " actual", image=self.output)
        self.assertEqual((self.root / "actual").read_bytes(), data)
        self.assertFalse(Path(str(self.output) + ".INVALID").exists())

    def test_production_policy_real_roundtrip(self):
        data = (Path(__file__).resolve().parents[1] /
                "stock/primary_audio_policy_configuration.xml").read_bytes()
        self.add_target(adaptation.POLICY, data)
        report = adaptation.execute(self.manifest, self.output, True)
        self.assertIn("e2fsck_after", report)
        self.dbg("dump " + adaptation.POLICY + " actual", image=self.output)
        self.assertEqual((self.root / "actual").read_bytes(), data)

    def test_shell_syntax_failure_does_not_publish(self):
        self.add_target(adaptation.LOADER, b"#!/system/bin/sh\nif then\n")
        import subprocess
        with self.assertRaises(subprocess.CalledProcessError):
            adaptation.execute(self.manifest, self.output, True)
        self.assertFalse(self.output.exists())

    def test_unimplemented_capture_policy_rejected(self):
        self.add_target(adaptation.POLICY, b'<module name="primary" halVersion="2.0">'
                        b'<mixPorts><mixPort role="sink"/></mixPorts></module>')
        with self.assertRaisesRegex(ValueError, "playback only"):
            adaptation.execute(self.manifest, self.output, True)
        self.assertFalse(self.output.exists())

    def test_other_scripts_and_policy_paths_still_rejected(self):
        original = copy.deepcopy(self.data)
        for target in ("/bin/load_audio.sh", "/lib/modules/other.sh",
                       "/vendor/etc/other.xml", "/data/load_audio.sh"):
            self.data = copy.deepcopy(original)
            self.data["replacements"][0]["path"] = target
            self.save()
            with self.subTest(target=target), self.assertRaisesRegex(ValueError, "Disallowed"):
                adaptation.execute(self.manifest, self.output, True)

    def test_non_elf_library_rejected_before_copy(self):
        with self.assertRaisesRegex(ValueError, "ELF32"):
            adaptation.execute(self.manifest, self.output, True)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()

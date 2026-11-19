#!/usr/bin/env python3
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("z1_odex_probe.sh")


class OdexProbeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="z1-odex-probe-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "system/bin"
        self.bin.mkdir(parents=True)
        for name in ("cat", "cut", "date", "grep", "mkdir", "sha256sum", "tail", "tee", "wc"):
            os.symlink(shutil.which(name), self.bin / name)
        self.target = self.root / "system/framework/oat/arm/services.odex"
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(b"cached-file")
        self.sample = b"direct-sample" * 1280
        self.helper = self.root / "system/bin/z1_odex_direct"
        self.helper.write_text("#!/bin/sh\ncat \"%s\"\n" % (self.root / "pages.bin"))
        self.helper.chmod(0o755)
        (self.root / "pages.bin").write_bytes(self.sample)
        self.command("id", 'printf "0\\n"')
        self.command("getprop", 'printf "1\\n"')
        self.command("pidof", 'printf "123\\n"')
        self.command("dmesg", 'printf "EXT4-fs error: sample\\n"')
        self.probe = self.root / "probe.sh"
        text = SCRIPT.read_text()
        text = re.sub(
            r'(?<![\w/])/(system|sbin|bin|proc|sys|data|cache)'
            r'(?=[/"\s:;])',
            lambda match: str(self.root) + match.group(),
            text,
        )
        self.probe.write_text(text)

    def command(self, name, body):
        target = self.bin / name
        target.write_text("#!/bin/sh\n" + body + "\n")
        target.chmod(0o755)

    def run_probe(self, expected_hash, expected_rc=0):
        env = os.environ.copy()
        env["PATH"] = str(self.bin)
        env["Z1_ODEX_EXPECTED"] = expected_hash
        result = subprocess.run(
            [shutil.which("dash"), str(self.probe)],
            text=True,
            capture_output=True,
            env=env,
            timeout=10,
        )
        self.assertEqual(result.returncode, expected_rc, result.stdout + result.stderr)
        return result.stdout

    def test_direct_sample_match_and_mismatch_are_explicit(self):
        digest = hashlib.sha256(self.sample).hexdigest()
        output = self.run_probe(digest)
        self.assertIn("result=DIRECT_MATCH_IMAGE", output)
        self.assertIn("kernel_storage_error_lines=1", output)
        self.assertIn("cached_sha256=" + hashlib.sha256(b"cached-file").hexdigest(), output)

        output = self.run_probe("0" * 64, expected_rc=1)
        self.assertIn("result=DIRECT_MISMATCH_IMAGE", output)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parents[2]
CANDIDATE = WORKSPACE / "out/r7_combined_candidate_20261119"
BOOT = CANDIDATE / "boot_hsq_control_r7.img"
SYSTEM = CANDIDATE / "system.img"
MANIFEST = HERE / "r7_runtime_manifest.json"


def sha256(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


@unittest.skipUnless(
    BOOT.is_file() and SYSTEM.is_file(),
    "R7 candidate images are not present",
)
class R7ImageManifestTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(MANIFEST.read_text())

    def debugfs(self, image, command):
        result = subprocess.run(
            ["debugfs", "-R", command, str(image)],
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout + result.stderr

    def extract(self, image, image_path):
        with tempfile.TemporaryDirectory(prefix="z1-image-read-") as directory:
            output = Path(directory) / "payload"
            self.debugfs(image, f"dump {image_path} {output}")
            self.assertTrue(output.is_file(), image_path)
            return output.read_bytes()

    def test_package_hashes(self):
        self.assertEqual(
            sha256(BOOT), self.manifest["package"]["bootimg"]["sha256"]
        )
        self.assertEqual(
            BOOT.stat().st_size,
            self.manifest["package"]["bootimg"]["bytes"],
        )
        self.assertEqual(
            sha256(SYSTEM), self.manifest["package"]["android"]["sha256"]
        )
        self.assertEqual(
            SYSTEM.stat().st_size,
            self.manifest["package"]["android"]["bytes"],
        )

    def test_every_runtime_file_matches_content_mode_and_owner(self):
        runtime_only = {
            "/hwcomposer.z1.so",
            "/data/local/tmp/z1stage/hwcomposer.z1.so.ramdisk",
        }
        for item in self.manifest["runtime_files"]:
            image_path = item["path"]
            if image_path in runtime_only:
                continue
            if image_path.startswith("/system/"):
                image_path = image_path.removeprefix("/system")
            with self.subTest(path=item["path"]):
                stat = self.debugfs(SYSTEM, f"stat {image_path}")
                if image_path == "/vendor/lib/hw/hwcomposer.z1.so" and \
                        "File not found" in stat:
                    # The boot installer publishes this path after /system mounts.
                    continue
                data = self.extract(SYSTEM, image_path)
                self.assertEqual(hashlib.sha256(data).hexdigest(), item["sha256"])
                self.assertIn("Type: regular", stat)
                self.assertRegex(stat, rf"Mode:\s+0*{re.escape(item['mode'])}\b")
                self.assertRegex(stat, r"User:\s+0\s+Group:\s+0\b")


if __name__ == "__main__":
    unittest.main()

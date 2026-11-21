#!/usr/bin/env python3
import hashlib
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
INSTALLER = HERE / "apply_audio_hal.sh"
HAL = HERE.parent / "r5g_audio" / "libaudio.primary.z1.so"
TARGETS = (
    "/vendor/lib/hw/audio.primary.mt6572.so",
    "/vendor/lib/libaudio.primary.default.so",
    "/vendor/lib/hw/audio.primary.default.so",
)


class AudioImageInstallerTest(unittest.TestCase):
    def debugfs(self, image, command, writable=False):
        args = ["debugfs"]
        if writable:
            args.append("-w")
        args.extend(["-R", command, str(image)])
        result = subprocess.run(args, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_replaces_all_hal_paths_and_preserves_file_metadata(self):
        with tempfile.TemporaryDirectory(prefix="z1-audio-image-") as directory:
            root = Path(directory)
            source = root / "in.img"
            output = root / "out.img"
            old = root / "old.so"
            source.write_bytes(b"")
            with source.open("r+b") as image:
                image.truncate(32 * 1024 * 1024)
            subprocess.run(["mkfs.ext4", "-Fq", str(source)], check=True)
            old.write_bytes(b"old")

            for path in ("/vendor", "/vendor/lib", "/vendor/lib/hw"):
                self.debugfs(source, f"mkdir {path}", writable=True)
            for target in TARGETS:
                self.debugfs(source, f"write {old} {target}", writable=True)
                self.debugfs(source, f"set_inode_field {target} mode 0100640", True)
                self.debugfs(source, f"set_inode_field {target} uid 12345", True)
                self.debugfs(source, f"set_inode_field {target} gid 23456", True)
                label = root / "selinux.label"
                label.write_bytes(b"u:object_r:vendor_file:s0\0")
                self.debugfs(
                    source,
                    f"ea_set -f {label} {target} security.selinux",
                    True,
                )

            env = os.environ.copy()
            env["Z1_MIN_FREE_GIB"] = "0"
            result = subprocess.run(
                [str(INSTALLER), "--hal", str(HAL), str(source), str(output)],
                text=True,
                capture_output=True,
                env=env,
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            expected_hash = hashlib.sha256(HAL.read_bytes()).hexdigest()
            for target in TARGETS:
                stat = self.debugfs(output, f"stat {target}")
                self.assertIn("Type: regular", stat)
                self.assertRegex(stat, r"Mode:\s+0640")
                self.assertRegex(stat, r"User:\s+12345")
                self.assertRegex(stat, r"Group:\s+23456")
                xattr = self.debugfs(output, f"ea_list {target}")
                self.assertIn("security.selinux", xattr)
                self.assertIn(r'u:object_r:vendor_file:s0\000"', xattr)
                dumped = root / target.replace("/", "_")
                self.debugfs(output, f"dump {target} {dumped}")
                self.assertEqual(hashlib.sha256(dumped.read_bytes()).hexdigest(), expected_hash)

            check = subprocess.run(["e2fsck", "-fn", str(output)])
            self.assertIn(check.returncode, (0, 1), "read-only fsck should be clean")


if __name__ == "__main__":
    unittest.main()

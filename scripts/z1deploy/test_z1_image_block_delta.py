#!/usr/bin/env python3
import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest


HERE = Path(__file__).resolve().parent
TOOL = HERE / "z1_image_block_delta.py"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ImageBlockDeltaTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="z1-delta-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = self.root / "base.img"
        self.target = self.root / "target.img"
        self.delta = self.root / "payload.z1delta"
        self.output = self.root / "output.img"
        base = bytearray(4096 * 4)
        target = bytearray(base)
        target[4096:8192] = b"B" * 4096
        target[12288 + 17:12288 + 33] = b"C" * 16
        self.base.write_bytes(base)
        self.target.write_bytes(target)

    def run_tool(self, *args):
        return subprocess.run(
            [str(TOOL), *map(str, args)],
            text=True,
            capture_output=True,
        )

    def create(self):
        result = self.run_tool(
            "create",
            "--base",
            self.base,
            "--target",
            self.target,
            "--output",
            self.delta,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"changed_blocks": 2', result.stdout)

    def test_create_and_apply_are_exact_and_refuse_overwrite(self):
        self.create()
        first = self.run_tool(
            "apply",
            "--base",
            self.base,
            "--delta",
            self.delta,
            "--output",
            self.output,
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(sha(self.output), sha(self.target))
        second = self.run_tool(
            "apply",
            "--base",
            self.base,
            "--delta",
            self.delta,
            "--output",
            self.output,
        )
        self.assertNotEqual(second.returncode, 0)
        self.assertIn("refusing to overwrite", second.stderr)

    def test_wrong_base_and_truncated_delta_are_rejected(self):
        self.create()
        wrong = self.root / "wrong.img"
        wrong.write_bytes(b"X" * self.base.stat().st_size)
        result = self.run_tool(
            "apply",
            "--base",
            wrong,
            "--delta",
            self.delta,
            "--output",
            self.output,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("base image SHA256 mismatch", result.stderr)
        self.assertFalse(self.output.exists())

        truncated = self.root / "truncated.z1delta"
        truncated.write_bytes(self.delta.read_bytes()[:-1])
        result = self.run_tool(
            "apply",
            "--base",
            self.base,
            "--delta",
            truncated,
            "--output",
            self.output,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("payload size mismatch", result.stderr)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()

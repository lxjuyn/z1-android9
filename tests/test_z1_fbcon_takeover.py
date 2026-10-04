import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "stock/z1_fbcon_takeover.sh"


class FbconTakeoverTests(unittest.TestCase):
    def add_console(self, root, index, name, bound):
        node = root / f"vtcon{index}"
        node.mkdir()
        (node / "name").write_text(name + "\n")
        (node / "bind").write_text(str(bound) + "\n")
        return node

    def run_helper(self, root):
        env = os.environ.copy()
        env["Z1_VTCONSOLE_ROOT"] = str(root)
        return subprocess.run(["/bin/sh", str(SCRIPT)], env=env,
                              capture_output=True, text=True)

    def test_unbinds_only_framebuffer_console_and_is_idempotent(self):
        with tempfile.TemporaryDirectory(prefix="z1-vtconsole-") as temp:
            root = Path(temp)
            dummy = self.add_console(root, 0, "dummy device", 1)
            fbcon = self.add_console(root, 1, "frame buffer device", 1)
            serial = self.add_console(root, 2, "serial device", 1)
            second_fbcon = self.add_console(root, 3, "frame buffer device", 1)

            first = self.run_helper(root)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual((fbcon / "bind").read_text().strip(), "0")
            self.assertEqual((second_fbcon / "bind").read_text().strip(), "0")
            self.assertEqual((dummy / "bind").read_text().strip(), "1")
            self.assertEqual((serial / "bind").read_text().strip(), "1")

            second = self.run_helper(root)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual((fbcon / "bind").read_text().strip(), "0")
            self.assertEqual((second_fbcon / "bind").read_text().strip(), "0")

    def test_no_framebuffer_console_is_a_safe_noop(self):
        with tempfile.TemporaryDirectory(prefix="z1-vtconsole-") as temp:
            root = Path(temp)
            dummy = self.add_console(root, 0, "dummy device", 1)
            result = self.run_helper(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((dummy / "bind").read_text().strip(), "1")

    def test_rejects_unrecognized_bind_state(self):
        with tempfile.TemporaryDirectory(prefix="z1-vtconsole-") as temp:
            root = Path(temp)
            fbcon = self.add_console(root, 1, "frame buffer device", 2)
            result = self.run_helper(root)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((fbcon / "bind").read_text().strip(), "2")


if __name__ == "__main__":
    unittest.main()

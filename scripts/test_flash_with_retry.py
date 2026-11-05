#!/usr/bin/env python3
"""Hardware-free parser and wrapper regressions. Never executes real mtkclient."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

TOOLS = Path(__file__).resolve().parent
PARSER = TOOLS / "flash_result_parser.sh"
WRAPPER = TOOLS / "flash_with_retry.sh"
REAL_LOG = Path("/tmp/flash_v12.log")
IMAGE = "/tmp/image [test].img"
SUCCESS = f"Wrote {IMAGE} to sector 137728 with sector count 1843200."
ERASE = "Formatted sector 137728 with sector count 1843200."
PARTIAL = "Done |----| 0.1% Write: (0x100000/0x38400000)\rProgress: 86.8% Write:\nUSBError(5)\nData ack failed for sdmmc_write_data"
NO_DEVICE = "No device found. Please reconnect."


class ParserTests(unittest.TestCase):
    def predicate(self, function, *args):
        result = subprocess.run(
            ["bash", "-c", 'source "$1"; shift; "$@"', "test", str(PARSER), function, *args],
            capture_output=True, text=True, check=False,
        )
        self.assertIn(result.returncode, (0, 1), result.stderr)
        return result.returncode == 0

    def test_write_matrix(self):
        cases = [
            (SUCCESS, "0", IMAGE, True),
            ("Done |----| 0.1% Write:\r" + SUCCESS, "0", IMAGE, True),
            ("\033[32m" + SUCCESS + "\033[0m\n", "0", IMAGE, True),
            (SUCCESS, "1", IMAGE, False),
            (SUCCESS, "0", "/tmp/other.img", False),
            (SUCCESS[:-1], "0", IMAGE, False),
            ("Done\nSuccessful\nfinished", "0", IMAGE, False),
            (PARTIAL, "0", IMAGE, False),
            (PARTIAL + "\n" + SUCCESS, "0", IMAGE, False),
            (SUCCESS + "\nUSBError(5)", "0", IMAGE, False),
            (SUCCESS + "\nUnexpected trailing output", "0", IMAGE, False),
            ("", "0", IMAGE, False),
            (NO_DEVICE, "0", IMAGE, False),
            (f"Wrote {IMAGE} to sector 1 with sector count 0.", "0", IMAGE, False),
        ]
        for out, rc, image, expected in cases:
            with self.subTest(out=out, rc=rc, image=image):
                self.assertEqual(self.predicate("mtk_write_succeeded", out, rc, image), expected)

    def test_connection_matrix(self):
        for out, expected in [(PARTIAL, True), (PARTIAL + "\nNo device found", True),
                              (NO_DEVICE, False), ("", True), ("truncated", True),
                              ("Main - Handling da commands ...\nNo device found", True),
                              (ERASE + "\ntimed out", True)]:
            with self.subTest(out=out):
                self.assertEqual(self.predicate("mtk_output_connected", out), expected)

    def test_erase_matrix(self):
        for out, rc, expected in [(ERASE, "0", True), (ERASE, "1", False),
                                  ("Done", "0", False), ("erase done", "0", False),
                                  ("Failed to format\n" + ERASE, "0", False),
                                  (ERASE + "\nUSBError(5)", "0", False)]:
            with self.subTest(out=out, rc=rc):
                self.assertEqual(self.predicate("mtk_erase_succeeded", out, rc), expected)

    @unittest.skipUnless(REAL_LOG.exists(), "optional observed hardware failure log unavailable")
    def test_observed_868_percent_failure(self):
        log = REAL_LOG.read_text()
        image = "/home/lxj/claude/6572/out/z1_v12_system/system_v12.img"
        self.assertIn("86.8%", log)
        self.assertIn("Data ack failed for sdmmc_write_data", log)
        self.assertFalse(self.predicate("mtk_write_succeeded", log, "0", image))
        self.assertTrue(self.predicate("mtk_output_connected", log))


class WrapperTests(unittest.TestCase):
    def run_mock(self, steps, verify="0", rollback=True):
        with tempfile.TemporaryDirectory(prefix="z1-flash-unit-") as directory:
            root = Path(directory)
            image, backup = root / "target.img", root / "rollback.img"
            image.write_bytes(b"target")
            backup.write_bytes(b"backup")
            # Only a copied wrapper with BOTH hardcoded paths replaced is executed.
            mock = root / "mock_mtk.py"
            mock.write_text('''#!/usr/bin/env python3
import json, pathlib, sys
root = pathlib.Path(__file__).parent
state = root / 'state'
i = int(state.read_text()) if state.exists() else 0
steps = json.loads((root / 'steps.json').read_text())
with (root / 'calls').open('a') as f:
    f.write(' '.join(sys.argv[1:]) + '\\n')
if i >= len(steps):
    print('ERROR: unexpected mock invocation')
    sys.exit(99)
step = steps[i]
state.write_text(str(i + 1))
if sys.argv[2] != step['op']:
    print('ERROR: wrong mock operation')
    sys.exit(98)
out = step['out'].replace('{target}', str(root / 'target.img')).replace('{rollback}', str(root / 'rollback.img'))
if 'readback' in step:
    pathlib.Path(sys.argv[4]).write_bytes(step['readback'].encode())
print(out)
sys.exit(step.get('rc', 0))
''')
            mock.chmod(0o755)
            (root / "steps.json").write_text(json.dumps(steps))
            source = WRAPPER.read_text()
            old_dir = "DIR=/home/lxj/claude/6572/mtkclient_v2141"
            old_py = "PY=/home/lxj/claude/6572/mtkclient_venv/bin/python"
            self.assertEqual(source.count(old_dir), 1)
            self.assertEqual(source.count(old_py), 1)
            source = source.replace(old_dir, f'DIR="{root}"').replace(old_py, f'PY="{mock}"')
            self.assertNotIn("mtkclient_venv", source)
            (root / "flash_result_parser.sh").write_text(PARSER.read_text())
            wrapper = root / "wrapper.sh"
            wrapper.write_text(source)
            (root / "sleep").write_text("#!/bin/sh\nexit 0\n")
            (root / "sleep").chmod(0o755)
            result = subprocess.run(
                ["bash", str(wrapper), "android", str(image), str(backup) if rollback else "", "1"],
                capture_output=True, text=True, timeout=20,
                env={**os.environ, "Z1_VERIFY": verify, "PATH": f"{root}:{os.environ['PATH']}"},
            )
            self.assertEqual(int((root / "state").read_text()), len(steps), result.stdout + result.stderr)
            calls = (root / "calls").read_text().splitlines()
            self.assertEqual(len(calls), len(steps), result.stdout + result.stderr)
            return result.returncode, result.stdout

    def test_command_confirmed_no_verify(self):
        rc, out = self.run_mock([erase(), write()])
        self.assertEqual(rc, 0)
        self.assertIn("未做读回校验", out)
        self.assertNotIn("读回一致", out)

    def test_verified_target(self):
        rc, out = self.run_mock([erase(), write(), readback("target")], verify="1")
        self.assertEqual(rc, 0)
        self.assertIn("写入及严格读回校验均成功", out)

    def test_verify_unavailable_not_success(self):
        rc, out = self.run_mock([erase(), write(), readback(None)], verify="1")
        self.assertEqual(rc, 4)
        self.assertIn("内容未验证", out)

    def test_failed_target_rolls_back_without_false_verified_claim(self):
        rc, out = self.run_mock([erase(), write(out=PARTIAL), write("rollback")])
        self.assertEqual(rc, 2)
        self.assertNotIn("读回一致", out)
        self.assertNotIn("已严格验证回滚", out)

    def test_mixed_target_failure_overrides_success(self):
        rc, _ = self.run_mock([erase(), write(out=PARTIAL + "\n" + terminal("target")), write("rollback")])
        self.assertEqual(rc, 2)

    def test_partial_rollback_stops(self):
        rc, _ = self.run_mock([erase(), write(out=PARTIAL), write("rollback", out=PARTIAL)])
        self.assertEqual(rc, 3)

    def test_wrong_image_rollback_stops(self):
        rc, _ = self.run_mock([erase(), write(out=PARTIAL), write("rollback", out=terminal("target"))])
        self.assertEqual(rc, 3)

    def test_successful_erase_then_no_connect_requires_rollback(self):
        rc, _ = self.run_mock([erase(), write(out=NO_DEVICE), write("rollback")])
        self.assertEqual(rc, 2)

    def test_clean_no_connect_does_not_rollback(self):
        rc, _ = self.run_mock([erase(out=NO_DEVICE), write(out=NO_DEVICE)])
        self.assertEqual(rc, 2)

    def test_truncated_target_requires_rollback(self):
        rc, _ = self.run_mock([erase(out=NO_DEVICE), write(out=""), write("rollback")])
        self.assertEqual(rc, 2)

    def test_erase_done_not_completion(self):
        rc, _ = self.run_mock([erase(out="Done"), write("rollback")])
        self.assertEqual(rc, 2)

    def test_erase_failure_overrides_completion(self):
        rc, _ = self.run_mock([erase(out="USBError(5)\n" + ERASE), write("rollback")])
        self.assertEqual(rc, 2)

    def test_partial_without_rollback_stops(self):
        rc, _ = self.run_mock([erase(), write(out=PARTIAL)], rollback=False)
        self.assertEqual(rc, 3)

    def test_failed_erase_without_rollback_stops(self):
        rc, _ = self.run_mock([erase(out="Failed to format sector 1.")], rollback=False)
        self.assertEqual(rc, 3)

    def test_rollback_verify_unavailable_not_claimed_verified(self):
        rc, out = self.run_mock([erase(), write(out=PARTIAL), write("rollback"), readback(None)], verify="1")
        self.assertEqual(rc, 2)
        self.assertIn("恢复内容尚未验证", out)
        self.assertNotIn("读回一致", out)

    def test_target_mismatch_rollback_verified(self):
        rc, out = self.run_mock([erase(), write(), readback("wrong"), write("rollback"), readback("backup")], verify="1")
        self.assertEqual(rc, 2)
        self.assertIn("回滚 完整分区读回完成", out)


def terminal(which):
    return f"Wrote {{{which}}} to sector 137728 with sector count 1843200."


def erase(out=ERASE):
    return {"op": "e", "out": out}


def write(which="target", out=None):
    return {"op": "w", "out": terminal(which) if out is None else out}


def readback(content):
    if content is None:
        return {"op": "r", "out": NO_DEVICE, "rc": 124}
    return {"op": "r", "out": "Read complete", "readback": content}


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""Fault-injection integration tests; only a temporary fake mtk.py is invoked."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

HERE = Path(__file__).resolve().parent
FAKE = r'''
import json, os, pathlib, sys, time
root = pathlib.Path(__file__).parent
op, offset, length, name = sys.argv[1:]
offset, length = int(offset, 0), int(length, 0)
assert op in ('wo', 'ro')
countsfile = root / 'counts.json'
counts = json.loads(countsfile.read_text()) if countsfile.exists() else {}
index = counts.get(op, 0)
counts[op] = index + 1
countsfile.write_text(json.dumps(counts))
plan = json.loads((root / 'plan.json').read_text())
actions = plan.get(op, [])
action = actions[index] if index < len(actions) else 'ok'
with (root / 'calls.jsonl').open('a') as f:
    f.write(json.dumps({'op': op, 'offset': offset, 'length': length, 'file': name, 'action': action})+'\n')
if action == 'sleep':
    time.sleep(5)
disk = root / 'device.bin'
if not disk.exists():
    disk.touch()
if op == 'wo':
    data = pathlib.Path(name).read_bytes()
    assert len(data) == length
    if action == 'corrupt_write':
        data = bytes([data[0] ^ 255]) + data[1:]
    with disk.open('r+b') as f:
        f.seek(offset)
        f.write(data)
    marker = f'Wrote {name} to offset {hex(offset)} with length {hex(length)}.'
else:
    with disk.open('rb') as f:
        f.seek(offset)
        data = f.read(length)
    if action == 'short': data = data[:-1]
    if action == 'long': data += b'!'
    if action == 'bad_bytes': data = bytes([data[0] ^ 255]) + data[1:]
    if action != 'missing': pathlib.Path(name).write_bytes(data)
    marker = f'Dumped offset {hex(offset)} with length {hex(length)} as {name}.'
if action == 'failed': print('ERROR: transfer aborted')
if action == 'wrong_marker': marker = marker.replace(hex(offset), hex(offset + 512))
if action != 'silent': print(marker)
sys.exit(7 if action == 'rc' else 0)
'''


class ChunkTransfers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="z1-chunk-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "mtk.py").write_text(FAKE)
        self.image = self.root / "image with spaces.img"
        self.image.write_bytes(bytes(range(256)) * 2)
        self.env = {**os.environ, "Z1_MTK_DIR": str(self.root), "Z1_MTK_PY": sys.executable,
                    "Z1_FLASH_LOCK": str(self.root / "device.lock"),
                    "Z1_CHUNK_STATE_DIR": str(self.root / "state"),
                    "Z1_CHUNK_LOG_DIR": str(self.root / "logs"),
                    "Z1_CHUNK_RETRY_DELAY": "0", "Z1_CHUNK_TIMEOUT": "2"}
        self.plan({})

    def plan(self, plan):
        (self.root / "plan.json").write_text(json.dumps(plan))
        (self.root / "counts.json").unlink(missing_ok=True)
        (self.root / "calls.jsonl").unlink(missing_ok=True)

    def command(self, mode="write", chunk="1", tries="1"):
        script = "z1_flash_chunked.sh" if mode == "write" else "z1_verify_chunks.sh"
        return ["bash", str(HERE / script), "bootimg", str(self.image), chunk, tries]

    def run_transfer(self, mode="write", chunk="1", tries="1"):
        return subprocess.run(self.command(mode, chunk, tries), env=self.env,
                              capture_output=True, text=True, timeout=15)

    def calls(self):
        path = self.root / "calls.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def state(self):
        files = list((self.root / "state").glob("*.json"))
        self.assertEqual(len(files), 1)
        return json.loads(files[0].read_text())

    def assert_incomplete(self):
        self.assertFalse(self.state()["complete"])
        self.assertEqual(self.state()["verified"], {})

    def test_full_transfer_and_partial_final_chunk(self):
        self.image.write_bytes(b"a" * 1048576 + b"b" * 512)
        result = self.run_transfer()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual([c["op"] for c in self.calls()], ["wo", "ro", "wo", "ro"])
        self.assertEqual([c["length"] for c in self.calls()], [1048576, 1048576, 512, 512])
        self.assertTrue(self.state()["complete"])
        self.assertEqual(len(self.state()["verified"]), 2)
        self.assertEqual(len(self.state()["identity"]["image_sha256"]), 64)

    def test_reject_bad_write_acknowledgements(self):
        for fault in ["rc", "failed", "wrong_marker", "silent"]:
            with self.subTest(fault=fault):
                self.plan({"wo": [fault]})
                result = self.run_transfer()
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertEqual([c["op"] for c in self.calls()], ["wo"])
                self.assert_incomplete()

    def test_reject_bad_readbacks_and_false_success(self):
        for fault in ["short", "long", "bad_bytes", "missing", "rc", "failed", "wrong_marker", "silent"]:
            with self.subTest(fault=fault):
                self.plan({"ro": [fault]})
                result = self.run_transfer()
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assert_incomplete()

    def test_retry_read_without_rewriting(self):
        self.plan({"ro": ["short", "ok"]})
        result = self.run_transfer(tries="2")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual([c["op"] for c in self.calls()], ["wo", "ro", "ro"])

    def test_retry_write_after_bad_ack(self):
        self.plan({"wo": ["rc", "ok"]})
        result = self.run_transfer(tries="2")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual([c["op"] for c in self.calls()], ["wo", "wo", "ro"])

    def test_retry_write_after_persistent_mismatch(self):
        self.plan({"wo": ["corrupt_write", "ok"]})
        result = self.run_transfer(tries="2")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual([c["op"] for c in self.calls()], ["wo", "ro", "ro", "wo", "ro"])

    def test_resume_rechecks_completed_blocks(self):
        self.image.write_bytes(b"a" * 1048576 + b"b" * 512)
        self.plan({"wo": ["ok", "rc"]})
        self.assertEqual(self.run_transfer().returncode, 2)
        self.assertEqual(list(self.state()["verified"]), ["0"])
        self.plan({})
        result = self.run_transfer()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual([c["op"] for c in self.calls()], ["ro", "wo", "ro"])

    def test_resume_repairs_changed_device(self):
        self.assertEqual(self.run_transfer().returncode, 0)
        with (self.root / "device.bin").open("r+b") as disk:
            disk.seek(0x2980000)
            disk.write(b"BAD")
        self.plan({})
        self.assertEqual(self.run_transfer().returncode, 0)
        self.assertEqual([c["op"] for c in self.calls()], ["ro", "wo", "ro"])

    def test_chunk_size_change_does_not_skip_bytes(self):
        self.image.write_bytes(b"x" * (2 * 1048576))
        self.assertEqual(self.run_transfer().returncode, 0)
        self.plan({})
        result = self.run_transfer(chunk="4")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual([c["op"] for c in self.calls()], ["wo", "ro"])
        self.assertEqual(self.calls()[0]["length"], 2 * 1048576)
        self.assertEqual(len(list((self.root / "state").glob("*.json"))), 2)

    def test_legacy_and_invalid_state_not_trusted(self):
        state = self.root / "state"
        state.mkdir()
        (state / "old.state").write_text("0\n1\n")
        self.assertEqual(self.run_transfer().returncode, 0)
        path = next(state.glob("*.json"))
        old = json.loads(path.read_text())
        old["identity"]["image_size"] += 512
        path.write_text(json.dumps(old))
        self.plan({})
        self.assertEqual(self.run_transfer().returncode, 0)
        self.assertEqual([c["op"] for c in self.calls()], ["wo", "ro"])

    def test_verify_is_read_only_and_checks_errors(self):
        self.assertEqual(self.run_transfer().returncode, 0)
        for fault, expected in [("ok", 0), ("rc", 2), ("short", 2), ("bad_bytes", 2)]:
            with self.subTest(fault=fault):
                self.plan({"ro": [fault]})
                result = self.run_transfer(mode="verify")
                self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
                self.assertEqual([c["op"] for c in self.calls()], ["ro"])

    def test_reject_invalid_geometry_before_device_access(self):
        for chunk, tries in [("0", "1"), ("-1", "1"), ("1", "0"), ("x", "1")]:
            self.assertNotEqual(self.run_transfer(chunk=chunk, tries=tries).returncode, 0)
        for data in [b"", b"x", b"x" * (6 * 1048576 + 512)]:
            self.image.write_bytes(data)
            self.assertNotEqual(self.run_transfer().returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_timeout_is_not_success_and_lock_is_released(self):
        self.plan({"wo": ["sleep"]})
        self.env["Z1_CHUNK_TIMEOUT"] = "0.1"
        self.assertEqual(self.run_transfer().returncode, 2)
        self.assert_incomplete()
        with open(self.env["Z1_FLASH_LOCK"], "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_concurrent_commands_and_old_entry_points_share_lock(self):
        self.plan({"wo": ["sleep"]})
        first = subprocess.Popen(self.command(), env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 3
            while not self.calls() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(self.calls(), "first transfer did not start")
            second = self.run_transfer(mode="verify")
            self.assertNotEqual(second.returncode, 0)
            self.assertIn("device lock", second.stderr)
            # Even a lock regression must never reach the real MTK backend.
            old_source = (HERE / "flash_with_retry.sh").read_text()
            old_source = old_source.replace("DIR=/home/lxj/claude/6572/mtkclient_v2141", f'DIR="{self.root}"')
            old_source = old_source.replace("PY=/home/lxj/claude/6572/mtkclient_venv/bin/python", f'PY="{sys.executable}"')
            self.assertNotIn("mtkclient_v2141", old_source)
            self.assertNotIn("mtkclient_venv", old_source)
            old_wrapper = self.root / "old_wrapper.sh"
            old_wrapper.write_text(old_source)
            (self.root / "flash_result_parser.sh").write_text((HERE / "flash_result_parser.sh").read_text())
            old = subprocess.run(["bash", str(old_wrapper), "bootimg", str(self.image)],
                                 env=self.env, capture_output=True, text=True, timeout=3)
            self.assertNotEqual(old.returncode, 0)
            self.assertIn("device lock", old.stderr)
            if (HERE / "z1deploy/z1flash_image.sh").exists():
                deployed = subprocess.run(["bash", str(HERE / "z1deploy/z1flash_image.sh"), str(self.image)],
                                          env=self.env, capture_output=True, text=True, timeout=3)
                self.assertEqual(deployed.returncode, 5)
                self.assertIn("another flash", deployed.stderr)
            self.assertEqual(len(self.calls()), 1)
        finally:
            first.terminate()
            first.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main(verbosity=2)

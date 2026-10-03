"""Exercise the real C formatter and fixed-file tombstone export on the host."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "z1_android9_bootdiag.c"


def records(data, label):
    result, current = [], b""
    for row in data.splitlines(keepends=True):
        prefix = b"[" + label + b"]"
        if not row.startswith(prefix):
            continue
        tag = row[len(prefix):len(prefix) + 1]
        current += row[len(prefix) + 2:-1]
        if tag == b"=":
            result.append(current)
            current = b""
    if current:
        raise AssertionError("unterminated fragment")
    return result


class StreamTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.fifo = cls.root / "stream"
        cls.tombs = cls.root / "tombstones"
        cls.tombs.mkdir()
        cls.exe = cls.root / "harness"
        cls.getprop = cls.root / "getprop"
        cls.mounts = cls.root / "mounts"
        cls.supplies = cls.root / "power_supply"
        cls.supplies.mkdir()
        code = cls.root / "harness.c"
        code.write_text(f'''#define DIAG_FIFO "{cls.fifo}"
#define TOMBSTONE_DIR "{cls.tombs}"
#define GETPROP_PATH "{cls.getprop}"
#define PROC_MOUNTS_PATH "{cls.mounts}"
#define POWER_SUPPLY_PATH "{cls.supplies}"
#define main bootdiag_main
#include "{SOURCE}"
#undef main
int main(int argc, char **argv) {{
    if (argc > 1 && !strcmp(argv[1], "tombs")) return tombstone_dump();
    if (argc > 1 && !strcmp(argv[1], "status")) return status_dump();
    return stream_text(STDOUT_FILENO, "z1logcat", STDIN_FILENO, 0, NULL) != 0;
}}
''')
        subprocess.run(["gcc", "-O2", "-Wall", "-Wextra", "-Werror",
                        str(code), "-o", str(cls.exe)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        for path in self.tombs.iterdir():
            path.unlink()
        if self.fifo.exists():
            self.fifo.unlink()
        import shutil
        if self.supplies.exists():
            shutil.rmtree(self.supplies)
        self.supplies.mkdir()
        self.getprop.write_text(f'''#!{sys.executable}
import sys, time
key = sys.argv[1]
if key == 'sys.boot_completed': print('1')
elif key == 'init.svc.surfaceflinger': print('running')
elif key == 'init.svc.zygote': print('x' * 1024)
elif key == 'init.svc.bootanim': time.sleep(3)
else: print('stopped')
''')
        self.getprop.chmod(0o755)
        self.mounts.write_text('/dev/block/mmcblk0p4 /system ext4 ro 0 0\n' * 1000)

    def capture_status(self):
        import time
        os.mkfifo(self.fifo, 0o600)
        reader = os.open(self.fifo, os.O_RDWR | os.O_NONBLOCK)
        child = subprocess.Popen([str(self.exe), 'status'])
        output = bytearray()
        deadline = time.monotonic() + 5
        try:
            while time.monotonic() < deadline:
                try:
                    output.extend(os.read(reader, 65536))
                except BlockingIOError:
                    pass
                if child.poll() is not None:
                    try:
                        output.extend(os.read(reader, 65536))
                    except BlockingIOError:
                        pass
                    break
                time.sleep(.005)
            self.assertEqual(child.wait(timeout=1), 0)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
            os.close(reader)
        self.assertLess(time.monotonic(), deadline)
        self.assertLess(len(output), 32768)
        return records(bytes(output), b'z1status')

    def test_status_snapshot_caps_properties_mounts_and_supplies(self):
        for i in range(10):
            supply = self.supplies / f'battery{i}'
            supply.mkdir()
            for name, value in [('temp', '400'), ('capacity', '50'), ('present', '1'), ('status', 'Discharging')]:
                (supply / name).write_text(value)
        rows = self.capture_status()
        self.assertIn(b'value=1', next(row for row in rows if b'key=sys.boot_completed' in row))
        self.assertIn(b'result=LIMIT', next(row for row in rows if b'key=init.svc.zygote' in row))
        self.assertIn(b'result=TIMEOUT', next(row for row in rows if b'key=init.svc.bootanim' in row))
        self.assertIn(b'MOUNTS END bytes=16384 limit_reached=1', rows)
        self.assertEqual(sum(row.startswith(b'POWER name=') for row in rows), 32)
        self.assertEqual(rows[-1], b'DONE supplies=8')

    def test_status_missing_system_and_paths_report_errors(self):
        self.getprop.unlink()
        self.mounts.unlink()
        self.supplies.rmdir()
        rows = self.capture_status()
        self.assertEqual(sum(b'result=EXIT_ERROR' in row for row in rows), 6)
        self.assertTrue(any(row.startswith(b'MOUNTS valid=0 errno=') for row in rows))
        self.assertTrue(any(row.startswith(b'POWER_DIR valid=0 errno=') for row in rows))
        self.assertEqual(rows[-1], b'DONE supplies=0')

    def test_long_lines_are_reconstructible_without_cropping(self):
        lines = [b"", b"a" * 145, b"b" * 3000, b"c" * 9001,
                 "中文\tUTF-8".encode() * 1200, b"last without newline"]
        output = subprocess.run([str(self.exe)], input=b"\n".join(lines),
                                capture_output=True, check=True, timeout=5).stdout
        self.assertEqual(records(output, b"z1logcat"), lines)
        self.assertNotIn(b"[truncated]", output)
        self.assertTrue(all(len(row) < 4096 for row in output.split(b"\n")))

    def test_tombstone_size_cap_is_reported(self):
        os.mkfifo(self.fifo, 0o600)
        cap = 4 * 1024 * 1024
        (self.tombs / "tombstone_00").write_bytes(b"x" * (cap + 10))
        reader = os.open(self.fifo, os.O_RDWR | os.O_NONBLOCK)
        child = subprocess.Popen([str(self.exe), "tombs"])
        output = bytearray()
        import time
        deadline = time.monotonic() + 5
        try:
            while time.monotonic() < deadline:
                try:
                    output.extend(os.read(reader, 65536))
                except BlockingIOError:
                    pass
                if child.poll() is not None:
                    try:
                        output.extend(os.read(reader, 65536))
                    except BlockingIOError:
                        pass
                    break
                time.sleep(.001)
            self.assertEqual(child.wait(timeout=1), 0)
        finally:
            os.close(reader)
        rows = records(bytes(output), b"z1tombstone")
        self.assertEqual(rows[1], b"x" * cap)
        self.assertIn(b"read=4194304 status=OK capped=1", rows[-2])

    def test_tombstones_full_export_and_refusal_of_symlink_and_fifo(self):
        os.mkfifo(self.fifo, 0o600)
        original = b"backtrace\n" + b"frame " * 1700 + b"\nEND ORIGINAL\n"
        (self.tombs / "tombstone_00").write_bytes(original)
        (self.tombs / "tombstone_01").symlink_to(self.tombs / "tombstone_00")
        os.mkfifo(self.tombs / "tombstone_02", 0o600)
        (self.tombs / "tombstone_99").write_text("must not export")
        reader = os.open(self.fifo, os.O_RDWR | os.O_NONBLOCK)
        try:
            child = subprocess.Popen([str(self.exe), "tombs"])
            output = bytearray()
            import time
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                try:
                    output.extend(os.read(reader, 65536))
                except BlockingIOError:
                    pass
                if child.poll() is not None:
                    try:
                        output.extend(os.read(reader, 65536))
                    except BlockingIOError:
                        pass
                    break
                time.sleep(.005)
            self.assertEqual(child.wait(timeout=1), 0)
        finally:
            os.close(reader)
        rows = records(bytes(output), b"z1tombstone")
        self.assertTrue(rows[0].startswith(b"BEGIN tombstone_00 size="))
        self.assertEqual(b"\n".join(rows[1:-2]) + b"\n", original)
        self.assertIn(b"status=OK capped=0", rows[-2])
        self.assertEqual(rows[-1], b"DONE files=1")


if __name__ == "__main__":
    unittest.main()

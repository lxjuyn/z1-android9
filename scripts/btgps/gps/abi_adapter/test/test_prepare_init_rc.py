#!/usr/bin/env python3
"""Host regression checks for the mnld init-block rewrite."""

import tempfile
import unittest
from pathlib import Path

import prepare_init_rc


OLD = (
    b"before\n"
    b"service z1_mnld /vendor/bin/mnld\n"
    b"    class main\n"
    b"    group system inet media net_bt\n"
    b"    socket mnld stream 0660 system system\n"
    b"    disabled\n"
    b"    oneshot\n"
    b"    seclabel u:r:init:s0\n"
    b"\n"
    b"on property:sys.z1.radio.modules_ready=1\n"
    b"    exec_background -- /system/bin/sh /z1_gps_prep.sh\n"
    b"after\n"
)


class PrepareInitRcTest(unittest.TestCase):
    def test_rewrites_service_and_disables_automatic_prep(self):
        output = prepare_init_rc.transform(OLD)
        self.assertIn(b"before\n", output)
        self.assertIn(b"after\n", output)
        self.assertIn(b"service mnld /vendor/bin/mnld\n", output)
        self.assertIn(b"group 1000 1050 1021 3003 9997 1001\n", output)
        self.assertIn(b"socket mnld stream 0660 gps system\n", output)
        self.assertNotIn(b"service z1_mnld ", output)
        self.assertNotIn(b"exec_background -- /system/bin/sh /z1_gps_prep.sh", output)
        self.assertEqual(
            output.count(b"on property:sys.z1.radio.modules_ready=1\n"), 1
        )

    def test_accepts_already_renamed_service(self):
        output = prepare_init_rc.transform(
            OLD.replace(b"service z1_mnld", b"service mnld")
        )
        self.assertEqual(output, prepare_init_rc.transform(OLD))

    def test_accepts_r7_without_automatic_prep(self):
        guarded = OLD.replace(
            b"on property:sys.z1.radio.modules_ready=1\n"
            b"    exec_background -- /system/bin/sh /z1_gps_prep.sh\n", b""
        )
        self.assertIn(b"service mnld /vendor/bin/mnld", prepare_init_rc.transform(guarded))

    def test_rejects_missing_duplicate_and_unsafe_service_blocks(self):
        for data in (
            b"",
            OLD + OLD,
            OLD.replace(b"/vendor/bin/mnld", b"/wrong/mnld"),
            OLD.replace(b"socket mnld stream", b"socket other stream"),
            OLD.replace(
                b"on property:sys.z1.radio.modules_ready=1\n",
                b"on property:another.trigger=1\n",
            ),
            OLD + b"\0",
        ):
            with self.subTest(data=data):
                with self.assertRaises(ValueError):
                    prepare_init_rc.transform(data)

    def test_file_round_trip(self):
        with tempfile.TemporaryDirectory(prefix="gps-rc-test-") as directory:
            root = Path(directory)
            source = root / "in.rc"
            target = root / "out.rc"
            source.write_bytes(OLD)
            result = prepare_init_rc.transform(source.read_bytes())
            target.write_bytes(result)
            self.assertEqual(target.read_bytes(), prepare_init_rc.transform(OLD))


if __name__ == "__main__":
    unittest.main()

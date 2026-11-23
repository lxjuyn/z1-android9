import importlib.util
import io
from pathlib import Path
import struct
import unittest


script = Path(__file__).resolve().parents[1] / 'scripts/btgps/bt/z1_bt_snoop_commands.py'
spec = importlib.util.spec_from_file_location('snoop_commands', script)
snoop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(snoop)
HEADER = b'btsnoop\0' + struct.pack('>II', 1, 1002)


def record(flags, packet, ts):
    return struct.pack('>IIIIQ', len(packet), len(packet), flags, 0, ts) + packet


class SnoopTest(unittest.TestCase):
    def test_acknowledgements_preserve_failure_and_outstanding_command(self):
        out = io.StringIO()
        capture = HEADER + b''.join([
            record(2, bytes.fromhex('01 03 0c 00'), 1000),
            record(3, bytes.fromhex('04 0e 04 01 03 0c 00'), 1010),
            record(2, bytes.fromhex('01 04 10 01 03'), 1020),
            record(3, bytes.fromhex('04 0f 04 30 01 04 10'), 1030),
            record(2, bytes.fromhex('01 09 10 00'), 1040),
            record(1, b'\x02\x00\x00\x06\x00secret', 1050),
        ])
        snoop.summarize(io.BytesIO(capture), out)
        self.assertIn('10us rx complete opcode=0x0c03 status=0x00 matched=1', out.getvalue())
        self.assertIn('30us rx status opcode=0x1004 status=0x30 matched=1', out.getvalue())
        self.assertIn('no_ack_in_capture opcode=0x1009 count=1', out.getvalue())
        self.assertNotIn('secret', out.getvalue())

    def test_damaged_or_empty_capture_is_inconclusive(self):
        for case in (HEADER, HEADER + b'x', HEADER + record(2, b'\x01\x03\x0c\x01', 1),
                     HEADER + record(3, b'\x04\x0e\x04\x01\x03\x0c\x00', 1)[:-1]):
            with self.subTest(case=case), self.assertRaises(ValueError):
                snoop.summarize(io.BytesIO(case), io.StringIO())

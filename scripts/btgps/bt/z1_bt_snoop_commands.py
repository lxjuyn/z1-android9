#!/usr/bin/env python3
"""Show HCI command opcodes and controller acknowledgements from a Pie H4 snoop."""
import argparse
from collections import Counter
import struct
import sys


HEADER = struct.Struct('>IIIIQ')


def summarize(stream, out):
    if stream.read(16) != b'btsnoop\x00' + struct.pack('>II', 1, 1002):
        raise ValueError('expected btsnoop v1 H4 capture')
    pending = Counter()
    commands = 0
    first_timestamp = None
    while header := stream.read(HEADER.size):
        if len(header) != HEADER.size:
            raise ValueError('incomplete record header')
        original, size, flags, dropped, timestamp = HEADER.unpack(header)
        if not 1 <= size <= original <= 65540:
            raise ValueError('invalid H4 record size')
        packet = stream.read(size)
        if len(packet) != size:
            raise ValueError('incomplete record payload')
        if first_timestamp is None:
            first_timestamp = timestamp
        relative_us = timestamp - first_timestamp
        if dropped or original != size:
            raise ValueError('incomplete capture: dropped or shortened record')
        if packet[0] == 1:
            if flags != 2 or size < 4 or size != 4 + packet[3]:
                raise ValueError('malformed command record')
            opcode = int.from_bytes(packet[1:3], 'little')
            commands += 1
            pending[opcode] += 1
            print(f'{relative_us}us tx opcode=0x{opcode:04x}', file=out)
        elif packet[0] == 4:
            if flags != 3 or size < 3 or size != 3 + packet[2]:
                raise ValueError('malformed event record')
            if packet[1] not in (0x0e, 0x0f):
                continue
            complete = packet[1] == 0x0e
            if size < (6 if complete else 7):
                raise ValueError('short command acknowledgement')
            pos = 4 if complete else 5
            opcode = int.from_bytes(packet[pos:pos + 2], 'little')
            status = (packet[6] if size >= 7 else None) if complete else packet[3]
            kind = 'complete' if complete else 'status'
            status_text = f'0x{status:02x}' if status is not None else 'absent'
            paired = pending[opcode] > 0
            if paired:
                pending[opcode] -= 1
            print(f'{relative_us}us rx {kind} opcode=0x{opcode:04x} '
                  f'status={status_text} matched={int(paired)}', file=out)
    if not commands:
        raise ValueError('no host HCI commands in this capture')
    for opcode, count in pending.items():
        if count:
            print(f'no_ack_in_capture opcode=0x{opcode:04x} count={count}', file=out)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture', type=argparse.FileType('rb'))
    args = parser.parse_args()
    try:
        summarize(args.capture, sys.stdout)
    except (ValueError, OSError) as exc:
        parser.exit(2, f'cannot summarize capture: {exc}\n')

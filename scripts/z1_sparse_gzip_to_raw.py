#!/usr/bin/env python3
"""Stream an archived Android sparse gzip into a new raw file, without a temporary image."""
import argparse
import gzip
from pathlib import Path
import struct
import zlib


def exact(stream, size):
    data = stream.read(size)
    if len(data) != size:
        raise ValueError('truncated sparse image')
    return data


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('source', type=Path)
    ap.add_argument('output', type=Path)
    args = ap.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(args.source, 'rb') as src, args.output.open('xb') as dst:
        magic, major, minor, header, chunk_header, block, blocks, chunks, checksum = struct.unpack(
            '<I4H4I', exact(src, 28))
        if magic != 0xed26ff3a or major != 1 or header < 28 or chunk_header < 12 or not block:
            raise ValueError('unsupported sparse header')
        exact(src, header - 28)
        total = 0
        crc = 0
        for _ in range(chunks):
            kind, reserved, count, length = struct.unpack('<2H2I', exact(src, 12))
            exact(src, chunk_header - 12)
            size = count * block
            payload = length - chunk_header
            if size > (blocks - total) * block:
                raise ValueError('chunk exceeds total block count')
            if kind == 0xcac1 and payload == size:
                while size:
                    data = exact(src, min(size, 1024 * 1024))
                    dst.write(data)
                    crc = zlib.crc32(data, crc)
                    size -= len(data)
            elif kind in (0xcac2, 0xcac3) and payload == (4 if kind == 0xcac2 else 0):
                pattern = exact(src, 4) if kind == 0xcac2 else b'\0' * 4
                buffer = pattern * (1024 * 1024 // 4)
                if kind == 0xcac3:
                    dst.seek(size, 1)
                while size:
                    data = buffer[:min(size, len(buffer))]
                    if kind == 0xcac2:
                        dst.write(data)
                    crc = zlib.crc32(data, crc)
                    size -= len(data)
            elif kind == 0xcac4 and count == 0 and payload == 4:
                if struct.unpack('<I', exact(src, 4))[0] != crc:
                    raise ValueError('sparse CRC mismatch')
            else:
                raise ValueError('unsupported or malformed sparse chunk')
            total += count
        if total != blocks or (checksum and checksum != crc) or src.read(1):
            raise ValueError('sparse length/checksum mismatch')
        dst.truncate(blocks * block)
        print('Raw image bytes:', blocks * block, 'CRC32:', hex(crc))


if __name__ == '__main__':
    main()

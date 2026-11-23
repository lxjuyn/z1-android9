#!/usr/bin/env python3
"""Skip the optional Android BLE offload probe on the pinned Z1 Pie stack."""
import argparse
import hashlib
from pathlib import Path


def build(source, output):
    data = bytearray(source.read_bytes())
    if hashlib.sha256(data).hexdigest() != "86f6c3f0de68d417273d7fc869712f2d01b993b7aa2e43e76336a9daee034833":
        raise ValueError("unexpected source library")
    # PT_LOAD VA is 0x7000. Branch directly to the native unsupported path.
    offset = 0x91084 - 0x7000
    data[offset:offset + 2] = bytes.fromhex("28e0")
    if hashlib.sha256(data).hexdigest() != "d49256ef3a4cd9e30a2e70d53ef2a2560c21c5d031830ae927cfdec00c6dc81f":
        raise ValueError("unexpected candidate digest")
    with output.open("xb") as dest:
        dest.write(data)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    build(args.source, args.output)

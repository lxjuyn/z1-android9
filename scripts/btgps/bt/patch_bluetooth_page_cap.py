#!/usr/bin/env python3
"""Cap Z1's Bluetooth feature pages at page 1 in the pinned R5 library."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import tempfile


BASE_SHA256 = "ff3988ec84087384a2ceb01a66e35282bfa1d34690f6a40be76717e122863b59"
TARGET_SHA256 = "86f6c3f0de68d417273d7fc869712f2d01b993b7aa2e43e76336a9daee034833"
FILE_OFFSET = 0x10B60C
ORIGINAL = bytes.fromhex("78782070")
PATCHED = bytes.fromhex("01202070")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build(base: Path, output: Path) -> None:
    if not base.is_file() or base.is_symlink():
        raise SystemExit(f"base is not a regular file: {base}")
    if output.exists() or output.is_symlink():
        raise SystemExit(f"refusing to overwrite: {output}")
    data = bytearray(base.read_bytes())
    actual = sha256(data)
    if actual != BASE_SHA256:
        raise SystemExit(f"base SHA256 mismatch: {actual}")
    if bytes(data[FILE_OFFSET:FILE_OFFSET + 4]) != ORIGINAL:
        raise SystemExit("unexpected bytes at the feature-page patch site")

    data[FILE_OFFSET:FILE_OFFSET + 2] = PATCHED[:2]
    result = bytes(data)
    actual = sha256(result)
    if actual != TARGET_SHA256:
        raise SystemExit(f"patched SHA256 mismatch: {actual}")
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as destination:
            destination.write(result)
            destination.flush()
            os.fsync(destination.fileno())
        os.link(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    build(args.base, args.output)
    print(f"base_sha256={BASE_SHA256}")
    print(f"target_sha256={TARGET_SHA256}")
    print(f"offset=0x{FILE_OFFSET:x}")
    print(f"bytes={ORIGINAL.hex()}->{PATCHED.hex()}")


if __name__ == "__main__":
    main()

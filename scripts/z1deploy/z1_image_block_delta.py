#!/usr/bin/env python3
"""Create or apply an exact same-size block delta for a Z1 system image."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile


MAGIC = b"Z1DELTA1\n"
BLOCK_SIZE = 4096
MAX_HEADER = 16 * 1024 * 1024


def sha256_path(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def regular_file(path: Path) -> None:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or path.is_symlink():
        raise ValueError(f"not a regular file: {path}")


def publish_temp(target: Path, writer) -> None:
    if target.exists() or target.is_symlink():
        raise ValueError(f"refusing to overwrite: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{target.name}.", dir=target.parent
    )
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "w+b") as output:
            writer(output)
            output.flush()
            os.fsync(output.fileno())
        os.link(temp, target)
        with target.open("rb") as published:
            os.fsync(published.fileno())
    finally:
        temp.unlink(missing_ok=True)


def create_delta(base: Path, target: Path, output: Path) -> dict:
    regular_file(base)
    regular_file(target)
    base_size = base.stat().st_size
    target_size = target.stat().st_size
    if base_size != target_size:
        raise ValueError("base and target sizes differ")
    if base_size % BLOCK_SIZE:
        raise ValueError("image size is not block aligned")

    changed: list[int] = []
    target_blocks: list[bytes] = []
    block_index = 0
    with base.open("rb") as old, target.open("rb") as new:
        while True:
            old_block = old.read(BLOCK_SIZE)
            new_block = new.read(BLOCK_SIZE)
            if not old_block and not new_block:
                break
            if len(old_block) != BLOCK_SIZE or len(new_block) != BLOCK_SIZE:
                raise ValueError("truncated or unaligned image block")
            if old_block != new_block:
                changed.append(block_index)
                target_blocks.append(new_block)
            block_index += 1

    header = {
        "version": 1,
        "block_size": BLOCK_SIZE,
        "image_size": base_size,
        "base_sha256": sha256_path(base),
        "target_sha256": sha256_path(target),
        "blocks": changed,
    }
    encoded_header = json.dumps(
        header, sort_keys=True, separators=(",", ":")
    ).encode("ascii") + b"\n"

    def write(output_file) -> None:
        output_file.write(MAGIC)
        output_file.write(encoded_header)
        for block in target_blocks:
            output_file.write(block)

    publish_temp(output, write)
    header["changed_blocks"] = len(changed)
    header["output_bytes"] = output.stat().st_size
    return header


def read_delta(delta: Path):
    regular_file(delta)
    with delta.open("rb") as source:
        if source.read(len(MAGIC)) != MAGIC:
            raise ValueError("bad delta magic")
        header_line = source.readline(MAX_HEADER)
        if not header_line.endswith(b"\n"):
            raise ValueError("truncated delta header")
        header = json.loads(header_line)
        expected_keys = {
            "version",
            "block_size",
            "image_size",
            "base_sha256",
            "target_sha256",
            "blocks",
        }
        if set(header) != expected_keys:
            raise ValueError("unexpected delta header fields")
        if header["version"] != 1 or header["block_size"] != BLOCK_SIZE:
            raise ValueError("unsupported delta version or block size")
        if (
            type(header["image_size"]) is not int
            or header["image_size"] <= 0
            or header["image_size"] % BLOCK_SIZE
        ):
            raise ValueError("invalid image size")
        blocks = header["blocks"]
        if not isinstance(blocks, list) or any(
            type(block) is not int
            or block < 0
            or block >= header["image_size"] // BLOCK_SIZE
            for block in blocks
        ):
            raise ValueError("invalid changed block index")
        if blocks != sorted(set(blocks)):
            raise ValueError("changed block indices are not strictly sorted")
        for name in ("base_sha256", "target_sha256"):
            if (
                not isinstance(header[name], str)
                or len(header[name]) != 64
                or any(character not in "0123456789abcdef" for character in header[name])
            ):
                raise ValueError(f"invalid {name}")
        payload_offset = len(MAGIC) + len(header_line)
        payload_bytes = len(blocks) * BLOCK_SIZE
        if delta.stat().st_size != payload_offset + payload_bytes:
            raise ValueError("delta payload size mismatch")
        source.seek(payload_offset)
        payload = source.read()
    return header, payload


def apply_delta(base: Path, delta: Path, output: Path) -> dict:
    regular_file(base)
    header, payload = read_delta(delta)
    if base.stat().st_size != header["image_size"]:
        raise ValueError("base image size mismatch")
    if sha256_path(base) != header["base_sha256"]:
        raise ValueError("base image SHA256 mismatch")

    def write(output_file) -> None:
        with base.open("rb") as source:
            shutil.copyfileobj(source, output_file, length=1024 * 1024)
        for index, block in zip(
            header["blocks"],
            (
                payload[offset:offset + BLOCK_SIZE]
                for offset in range(0, len(payload), BLOCK_SIZE)
            ),
        ):
            output_file.seek(index * BLOCK_SIZE)
            output_file.write(block)

    publish_temp(output, write)
    actual = sha256_path(output)
    if actual != header["target_sha256"]:
        output.unlink()
        raise ValueError(
            f"applied image SHA256 mismatch: expected={header['target_sha256']} actual={actual}"
        )
    return {
        "status": "PASS",
        "base_sha256": header["base_sha256"],
        "target_sha256": actual,
        "image_size": header["image_size"],
        "changed_blocks": len(header["blocks"]),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("--base", required=True, type=Path)
    create.add_argument("--target", required=True, type=Path)
    create.add_argument("--output", required=True, type=Path)
    apply = subparsers.add_parser("apply")
    apply.add_argument("--base", required=True, type=Path)
    apply.add_argument("--delta", required=True, type=Path)
    apply.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.command == "create":
            result = create_delta(args.base, args.target, args.output)
        else:
            result = apply_delta(args.base, args.delta, args.output)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"z1_image_block_delta: {error}", file=os.sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

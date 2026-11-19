#!/usr/bin/env python3
"""Inspect or extract allowlisted records from a Z1 MT6572 NVRAM image.

The source image is opened read-only and is never modified. The extractor
validates the primary and backup TOCs, the whole NVRAM content checksum, the
record extents, and the trailing checksums on the two allowlisted AP records
before publishing any data.

Only the WLAN and Bluetooth AP records are eligible for extraction. Factory
IMEI and radio calibration records are parsed for integrity but are neither
listed nor written. The final 16-bit storage checksum is removed so each
output file has the byte length expected by the Android runtime consumer.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import sys
import tempfile


IMAGE_SIZE = 0x500000
TOC_SIZE = 0x20000
HEADER_SIZE = 0x10
ENTRY_SIZE = 0x8C
ENTRY_COUNT = 40
HEADER_BLOCK_SIZE = HEADER_SIZE + ENTRY_SIZE * ENTRY_COUNT
BACKUP_AREA_OFFSET = IMAGE_SIZE - 0xC0000
BACKUP_MAGIC_OFFSET = BACKUP_AREA_OFFSET + 0x69
BACKUP_MAGIC = b"\xdd\xcc\xbb\xaa"
BACKUP_HEADER_OFFSET = BACKUP_MAGIC_OFFSET + len(BACKUP_MAGIC)
PATH_OFFSET_IN_ENTRY = 0x30


@dataclass(frozen=True)
class RecordSpec:
    label: str
    path: str
    payload_size: int
    storage_size: int


@dataclass(frozen=True)
class Record:
    index: int
    path: str
    offset: int
    size: int
    storage: bytes


ALLOWLIST = (
    RecordSpec(
        "WIFI",
        "/data/nvram/APCFG/APRDEB/WIFI",
        512,
        514,
    ),
    RecordSpec(
        "BT_Addr",
        "/data/nvram/APCFG/APRDEB/BT_Addr",
        64,
        66,
    ),
)


def checksum_nvram(data: bytes) -> int:
    """Return the legacy MTK whole-NVRAM checksum."""
    value = 0
    full = len(data) - len(data) % 4
    for index in range(0, full, 4):
        word = struct.unpack_from("<I", data, index)[0]
        if (index // 4) % 2 == 0:
            value ^= word
        else:
            value = (value + word) & 0xFFFFFFFF
    if full != len(data):
        value = (value + int.from_bytes(data[full:], "little")) & 0xFFFFFFFF
    return (value ^ len(data)) & 0xFFFFFFFF


def checksum_record(data: bytes) -> int:
    """Return the legacy MTK two-byte record checksum."""
    value = 0
    for index, byte in enumerate(data):
        if index % 2 == 0:
            value = (value + byte) & 0xFF
        else:
            value ^= byte
    return (0xAA << 8) | value


def _validate_runtime_path(path: str) -> None:
    if not path.startswith("/data/nvram/"):
        raise ValueError(f"Unexpected NVRAM record root: {path!r}")
    parts = path.split("/")[1:]
    if any(not part or part in (".", "..") for part in parts):
        raise ValueError(f"Unsafe NVRAM record path: {path!r}")
    if any(ord(character) < 0x20 or ord(character) > 0x7E
           for character in path):
        raise ValueError(f"Non-ASCII NVRAM record path: {path!r}")


def parse_image(data: bytes) -> tuple[dict[str, Record], str]:
    if len(data) != IMAGE_SIZE:
        raise ValueError(
            f"Expected a {IMAGE_SIZE}-byte Z1 NVRAM image, got {len(data)}"
        )

    primary = data[:HEADER_BLOCK_SIZE]
    if data[BACKUP_MAGIC_OFFSET:BACKUP_HEADER_OFFSET] != BACKUP_MAGIC:
        raise ValueError("Backup TOC magic is missing")
    backup = data[
        BACKUP_HEADER_OFFSET:BACKUP_HEADER_OFFSET + HEADER_BLOCK_SIZE
    ]
    if primary != backup:
        raise ValueError("Primary and backup NVRAM TOCs differ")

    content_size = struct.unpack_from("<I", primary, 4)[0]
    if content_size < 2 or TOC_SIZE + content_size > BACKUP_AREA_OFFSET:
        raise ValueError("NVRAM content size is outside the data area")
    content = data[TOC_SIZE:TOC_SIZE + content_size]
    declared_checksum = struct.unpack_from("<I", primary, 0x0C)[0]
    actual_checksum = checksum_nvram(content)
    if declared_checksum != actual_checksum:
        raise ValueError(
            "Whole-NVRAM checksum mismatch: "
            f"declared 0x{declared_checksum:08x}, "
            f"actual 0x{actual_checksum:08x}"
        )

    records: dict[str, Record] = {}
    expected_offset = 0
    for index in range(ENTRY_COUNT):
        entry = HEADER_SIZE + index * ENTRY_SIZE
        path_start = entry + PATH_OFFSET_IN_ENTRY
        path_field = data[path_start:entry + ENTRY_SIZE]
        terminator = path_field.find(b"\0")
        if terminator < 0:
            raise ValueError(f"TOC entry {index} has no NUL-terminated path")
        if terminator == 0:
            if any(path_field):
                raise ValueError(f"TOC entry {index} has a malformed empty path")
            break
        try:
            path = path_field[:terminator].decode("ascii")
        except UnicodeDecodeError as error:
            raise ValueError(f"TOC entry {index} path is not ASCII") from error
        _validate_runtime_path(path)
        if path in records:
            raise ValueError(f"Duplicate NVRAM record path: {path}")

        offset, size = struct.unpack_from("<II", data, path_start - 8)
        if size < 2 or offset != expected_offset:
            raise ValueError(
                f"NVRAM record {path!r} is not the expected contiguous block"
            )
        if offset + size > content_size:
            raise ValueError(f"NVRAM record is outside content: {path}")
        storage = content[offset:offset + size]
        records[path] = Record(index, path, offset, size, storage)
        expected_offset += size

    if expected_offset != content_size:
        raise ValueError(
            "NVRAM TOC does not cover the full declared content area"
        )
    return records, hashlib.sha256(b"primary\0" + primary).hexdigest()


def approved_payloads(
    records: dict[str, Record],
) -> dict[str, bytes]:
    payloads: dict[str, bytes] = {}
    for spec in ALLOWLIST:
        record = records.get(spec.path)
        if record is None:
            raise ValueError(f"Required NVRAM record is absent: {spec.path}")
        if record.size != spec.storage_size:
            raise ValueError(
                f"NVRAM record {spec.path} has {record.size} bytes; "
                f"expected {spec.storage_size}"
            )
        stored_checksum = struct.unpack_from(
            ">H", record.storage, record.size - 2
        )[0]
        calculated_checksum = checksum_record(record.storage[:-2])
        if stored_checksum != calculated_checksum:
            raise ValueError(f"NVRAM record checksum mismatch: {spec.path}")
        payload = record.storage[:-2]
        if len(payload) != spec.payload_size:
            raise ValueError(f"Unexpected payload size for {spec.path}")
        payloads[spec.label] = payload
    return payloads


def make_report(
    data: bytes,
    records: dict[str, Record],
    toc_digest: str,
    *,
    extracted: bool,
) -> dict:
    report = {
        "status": (
            "EXTRACTED_READ_ONLY_SOURCE"
            if extracted
            else "INSPECTED_READ_ONLY"
        ),
        "source_modified": False,
        "image_bytes": len(data),
        "toc_digest": toc_digest,
        "checks": {
            "primary_toc": "PASS",
            "backup_toc": "PASS",
            "whole_nvram_checksum": "PASS",
            "allowlisted_record_checksums": "PASS",
        },
        "records": {},
        "excluded_record_classes": [
            "IMEI",
            "radio calibration",
            "all paths outside the extraction allowlist",
        ],
        "restore_flag_created": False,
    }
    for spec in ALLOWLIST:
        record = records.get(spec.path)
        report["records"][spec.label] = {
            "available": record is not None,
            "runtime_path": spec.path,
            "payload_bytes": spec.payload_size,
            "storage_checksum_removed": record is not None,
        }
    return report


def read_readonly(path: Path) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise ValueError(f"Input is not a regular file: {path}")
        chunks = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
        if (
            opened.st_dev != after.st_dev
            or opened.st_ino != after.st_ino
            or opened.st_size != after.st_size
            or opened.st_mtime_ns != after.st_mtime_ns
        ):
            raise ValueError("Input changed while it was being read")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _mkdir_private(path: Path) -> None:
    path.mkdir(mode=0o700)
    os.chmod(path, 0o700)


def _write_private(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("Short write while extracting NVRAM record")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def extract(output: Path, payloads: dict[str, bytes]) -> None:
    if os.path.lexists(output):
        raise ValueError(f"Output already exists: {output}")
    if not output.parent.is_dir():
        raise ValueError(f"Output parent is not a directory: {output.parent}")

    staging = Path(tempfile.mkdtemp(
        prefix=f".{output.name}.partial-",
        dir=output.parent,
    ))
    os.chmod(staging, 0o700)
    published = False
    try:
        apcfg = staging / "APCFG"
        aprdeb = apcfg / "APRDEB"
        _mkdir_private(apcfg)
        _mkdir_private(aprdeb)
        _write_private(aprdeb / "WIFI", payloads["WIFI"])
        _write_private(aprdeb / "BT_Addr", payloads["BT_Addr"])
        os.rename(staging, output)
        published = True
    finally:
        if not published:
            shutil.rmtree(staging, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path, help="raw 5 MiB Z1 NVRAM image")
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="create this directory and place APCFG/APRDEB records in it",
    )
    args = parser.parse_args(argv)

    try:
        data = read_readonly(args.image)
        records, toc_digest = parse_image(data)
        payloads = approved_payloads(records)
        if args.output_dir is not None:
            extract(args.output_dir, payloads)
        report = make_report(
            data,
            records,
            toc_digest,
            extracted=args.output_dir is not None,
        )
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError) as error:
        print(f"ERROR {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

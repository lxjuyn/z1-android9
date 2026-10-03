#!/usr/bin/env python3
"""Extract the single generic MT6572 audio HAL from a local Z1 ROM ZIP.

Only the fixed 32-bit ARM HAL path is copied. NVRAM, audio calibration,
userdata, logs, and every other archive entry stay out of the build inputs.
"""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import stat
import struct
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_PATH = "system/lib/hw/audio.primary.mt6572.so"
OUTPUT_PATH = "lib/hw/audio.primary.mt6572.so"
MAX_FILE_SIZE = 256 * 1024


def check_arm_shared_object(data):
    if len(data) < 20 or data[:4] != b"\x7fELF":
        raise SystemExit("audio HAL is not an ELF shared object")
    if data[4] != 1 or data[5] != 1:
        raise SystemExit("audio HAL must be 32-bit little-endian")
    elf_type, machine = struct.unpack_from("<HH", data, 16)
    if elf_type != 3 or machine != 40:
        raise SystemExit("audio HAL must be an ARM shared object")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rom", type=Path, required=True,
                        help="local Z1 Android ROM ZIP containing system/ files")
    parser.add_argument("--output", type=Path,
                        default=ROOT / "proprietary/mt6572/audio",
                        help="ignored audio build-input directory")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise SystemExit(f"refusing to overwrite existing output: {output}")
    if not args.rom.is_file():
        raise SystemExit("ROM ZIP does not exist")

    with zipfile.ZipFile(args.rom) as archive:
        info = next((entry for entry in archive.infolist()
                     if entry.filename == ARCHIVE_PATH), None)
        if info is None:
            raise SystemExit(f"ROM is missing required audio HAL: {ARCHIVE_PATH}")
        if info.file_size <= 0 or info.file_size > MAX_FILE_SIZE:
            raise SystemExit("unexpected audio HAL size")
        if stat.S_ISLNK(info.external_attr >> 16):
            raise SystemExit("refusing symlink archive entry")
        data = archive.read(info)
        if len(data) != info.file_size:
            raise SystemExit("truncated audio HAL archive entry")
    check_arm_shared_object(data)

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".z1-audio-", dir=output.parent) as temp:
        stage = Path(temp)
        target = stage / OUTPUT_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(0o644)
        manifest = {
            "files": [{
                "archive_entry": ARCHIVE_PATH,
                "output": OUTPUT_PATH,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }]
        }
        (stage / "EXTRACTION-MANIFEST.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        shutil.copytree(stage, output)

    print(f"Extracted the whitelisted MT6572 audio HAL to {output}")
    print("No NVRAM, audio calibration, userdata, or logs were copied.")


if __name__ == "__main__":
    main()

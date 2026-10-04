#!/usr/bin/env python3
"""Extract only the generic MT6572 Bluetooth driver library from a local ROM.

The private NVRAM, Bluetooth address, Wi-Fi calibration, and user data are
never read or copied. The result is written under the ignored proprietary tree.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
IMAGE_PATH = "/lib/libbluetoothdrv.so"
OUTPUT_NAME = "libbluetoothdrv.so"
MAX_SIZE = 256 * 1024


def run(command):
    return subprocess.run(command, check=True, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE).stdout


def validate_blob(path):
    data = path.read_bytes()
    if len(data) < 52 or len(data) > MAX_SIZE:
        raise SystemExit("unexpected factory Bluetooth library size")
    if data[:6] != b"\x7fELF\x01\x01":
        raise SystemExit("Bluetooth library must be a 32-bit little-endian ELF")
    if int.from_bytes(data[16:18], "little") != 3 or int.from_bytes(data[18:20], "little") != 40:
        raise SystemExit("Bluetooth library must be an ARM shared object")
    symbols = run(["readelf", "--dyn-syms", "--wide", str(path)])
    for symbol in ("mtk_bt_enable", "mtk_bt_disable"):
        if not re.search(r"\b" + symbol + r"$", symbols, re.M):
            raise SystemExit("factory library is missing required symbol " + symbol)
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--system-image", type=Path, required=True,
                        help="local raw ext4 factory system.img")
    parser.add_argument("--output", type=Path,
                        default=ROOT / "proprietary/mt6572/bluetooth",
                        help="ignored proprietary output directory")
    args = parser.parse_args()
    if not args.system_image.is_file():
        raise SystemExit("factory system image does not exist")
    output = args.output.resolve()
    if output.exists():
        raise SystemExit("refusing to overwrite existing output: " + str(output))

    stat_text = run(["debugfs", "-R", "stat " + IMAGE_PATH,
                     str(args.system_image)])
    if "Type: regular" not in stat_text:
        raise SystemExit("whitelisted Bluetooth library is not a regular file")
    size_match = re.search(r"\bSize:\s*(\d+)", stat_text)
    if size_match is None or int(size_match.group(1)) <= 0 or int(size_match.group(1)) > MAX_SIZE:
        raise SystemExit("factory Bluetooth library has an invalid size")

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="z1-bt-blob-") as directory:
        staging = Path(directory)
        extracted = staging / OUTPUT_NAME
        run(["debugfs", "-R", "dump " + IMAGE_PATH + " " + str(extracted),
             str(args.system_image)])
        data = validate_blob(extracted)
        if len(data) != int(size_match.group(1)):
            raise SystemExit("factory Bluetooth library extraction was truncated")
        manifest = {
            "files": [{
                "source_entry": "/system" + IMAGE_PATH,
                "output": OUTPUT_NAME,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }],
        }
        (staging / "EXTRACTION-MANIFEST.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        shutil.copytree(staging, output)

    print("Extracted one generic 32-bit ARM Bluetooth driver library.")
    print("No device NVRAM, address, calibration, or userdata was read or copied.")


if __name__ == "__main__":
    main()

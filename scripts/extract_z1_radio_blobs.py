#!/usr/bin/env python3
"""Extract only generic MT6572 WMT firmware/tools from a local ROM ZIP.

No NVRAM, calibration database, MAC address, userdata, or arbitrary archive
paths are copied. Output stays under the ignored proprietary/ build-input tree.
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
FILES = {
    "system/bin/wmt_loader": ("bin/wmt_loader", 0o755),
    "system/bin/6620_launcher": ("bin/6620_launcher", 0o755),
    "system/etc/firmware/WMT_SOC.cfg": ("etc/firmware/WMT_SOC.cfg", 0o644),
    "system/etc/firmware/WIFI_RAM_CODE_SOC": ("etc/firmware/WIFI_RAM_CODE_SOC", 0o644),
    "system/etc/firmware/mt6572_82_patch_e1_0_hdr.bin": (
        "etc/firmware/mt6572_82_patch_e1_0_hdr.bin", 0o644),
    "system/etc/firmware/mt6572_82_patch_e1_1_hdr.bin": (
        "etc/firmware/mt6572_82_patch_e1_1_hdr.bin", 0o644),
}
MAX_FILE_SIZE = 512 * 1024
ARM_EXECUTABLES = {"bin/wmt_loader", "bin/6620_launcher"}


def check_arm_executable(relative, data):
    if len(data) < 20 or data[:4] != b"\x7fELF":
        raise SystemExit(f"radio tool is not an ELF executable: {relative}")
    if data[4] != 1 or data[5] != 1:
        raise SystemExit(f"radio tool must be 32-bit little-endian: {relative}")
    elf_type, machine = struct.unpack_from("<HH", data, 16)
    if elf_type not in (2, 3) or machine != 40:
        raise SystemExit(f"radio tool must be an ARM executable: {relative}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rom", type=Path, required=True,
                        help="local Z1 Android ROM ZIP containing system/ files")
    parser.add_argument("--output", type=Path,
                        default=ROOT / "proprietary/mt6572",
                        help="ignored build-input directory (default: proprietary/mt6572)")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise SystemExit(f"refusing to overwrite existing output: {output}")
    if not args.rom.is_file():
        raise SystemExit("ROM ZIP does not exist")

    with zipfile.ZipFile(args.rom) as archive:
        infos = {entry.filename: entry for entry in archive.infolist()}
        missing = sorted(set(FILES) - infos.keys())
        if missing:
            raise SystemExit("ROM is missing required generic radio files: " + ", ".join(missing))
        payloads = {}
        for source, (relative, mode) in FILES.items():
            info = infos[source]
            if info.file_size <= 0 or info.file_size > MAX_FILE_SIZE:
                raise SystemExit(f"unexpected size for whitelisted file {source}")
            unix_mode = info.external_attr >> 16
            if stat.S_ISLNK(unix_mode):
                raise SystemExit(f"refusing symlink entry {source}")
            data = archive.read(info)
            if len(data) != info.file_size:
                raise SystemExit(f"truncated archive entry {source}")
            if relative in ARM_EXECUTABLES:
                check_arm_executable(relative, data)
            payloads[relative] = (data, mode, source)

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".z1-radio-", dir=output.parent) as temp:
        stage = Path(temp)
        manifest = {"files": []}
        for relative, (data, mode, source) in sorted(payloads.items()):
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            target.chmod(mode)
            manifest["files"].append({
                "archive_entry": source,
                "output": relative,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            })
        (stage / "EXTRACTION-MANIFEST.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        shutil.copytree(stage, output)

    print(f"Extracted {len(FILES)} whitelisted generic radio files to {output}")
    print("No NVRAM, MAC address, calibration database, or userdata was extracted.")


if __name__ == "__main__":
    main()

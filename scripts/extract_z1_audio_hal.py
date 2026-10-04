#!/usr/bin/env python3
"""Extract the MT6572 audio wrapper and its vendor backend from a local Z1 ROM ZIP.

Backend extraction is explicitly opt-in; the default still extracts only the wrapper.
Only fixed 32-bit ARM library paths are copied. These include generic NVRAM
access code, never NVRAM contents, audio calibration, userdata or logs.
Old framework libraries are excluded. The reference backend audit has DT_NEEDED
closure PASS but strong-symbol ABI link FAIL (10 symbols); do not flash this bundle.
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
# The small HAL dlopens libaudio.primary.default.so, which is not DT_NEEDED.
# Fixed vendor-code closure from the same ROM as the wrapper. Do not copy old
# libmedia/libbinder/libutils or other framework libraries over Android 9.
# libbluetoothdrv.so is shared with the existing separately extracted BT input.
VENDOR_LIBRARIES = (
    "libaudio.primary.default.so",
    "libblisrc.so",
    "libnvram.so",
    "libspeech_enh_lib.so",
    "libaudiocustparam.so",
    "libaudiosetting.so",
    "libaudiocompensationfilter.so",
    "libcvsd_mtk.so",
    "libmsbc_mtk.so",
    "libaudiocomponentengine.so",
    "libaudiodcrflt.so",
    "libaed.so",
    "libcustom_nvram.so",
    "libmtk_drvb.so",
    "libnvram_sec.so",
    "libnvram_platform.so",
    "libbessound_mtk.so",
    "libblisrc32.so",
    "libbessound_hd_mtk.so",
    "libmtklimiter.so",
    "libmtkshifter.so",
)
FILES = (("system/lib/hw/audio.primary.mt6572.so", "lib/hw/audio.primary.mt6572.so"),) + tuple(
    ("system/lib/" + name, "lib/" + name) for name in VENDOR_LIBRARIES)
REFERENCE_BACKEND_SHA256 = "e5c14b471abce13ae72bb3a5b97474915fabef294a04983447ab70b0a36f4b75"
MAX_FILE_SIZE = 4 * 1024 * 1024
MAX_TOTAL_SIZE = 8 * 1024 * 1024


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
    parser.add_argument("--include-unverified-backend", action="store_true",
                        help="opt in to diagnostic vendor DSOs; known Pie ABI link FAIL, not flashable")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise SystemExit(f"refusing to overwrite existing output: {output}")
    if not args.rom.is_file():
        raise SystemExit("ROM ZIP does not exist")

    extracted = []
    total = 0
    with zipfile.ZipFile(args.rom) as archive:
        entries = archive.infolist()
        selected = FILES if args.include_unverified_backend else FILES[:1]
        for archive_path, output_path in selected:
            matches = [entry for entry in entries if entry.filename == archive_path]
            if len(matches) != 1:
                raise SystemExit(f"ROM requires one library entry: {archive_path}")
            info = matches[0]
            if info.file_size <= 0 or info.file_size > MAX_FILE_SIZE:
                raise SystemExit(f"unexpected audio library size: {archive_path}")
            if stat.S_ISLNK(info.external_attr >> 16) or info.is_dir():
                raise SystemExit(f"refusing non-file archive entry: {archive_path}")
            total += info.file_size
            if total > MAX_TOTAL_SIZE:
                raise SystemExit("audio library whitelist exceeds total size bound")
            data = archive.read(info)
            if len(data) != info.file_size:
                raise SystemExit(f"truncated audio library: {archive_path}")
            check_arm_shared_object(data)
            extracted.append((archive_path, output_path, data))

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".z1-audio-", dir=output.parent) as temp:
        stage = Path(temp)
        files = []
        for archive_path, output_path, data in extracted:
            target = stage / output_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            target.chmod(0o644)
            files.append({"archive_entry": archive_path, "output": output_path,
                          "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        manifest = {
            "files": files,
            "dynamic_backend": "libaudio.primary.default.so",
            "shared_bluetooth_dependency": "libbluetoothdrv.so (separate BT build input)",
            "device_runtime_verified": False,
            "pie_symbol_abi_verified": False,
            "unverified_backend_requested": args.include_unverified_backend,
            "safe_to_flash": False,
            "reference_backend_sha256": REFERENCE_BACKEND_SHA256,
            "reference_s1r_audit": {
                "dt_needed_closure": "PASS (88 libraries, no missing DSO)",
                "strong_symbol_abi_link": "FAIL (10 unresolved symbols)",
                "scope": "reference backend SHA above with S1R; not a validation of this extraction",
            },
        }
        (stage / "EXTRACTION-MANIFEST.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        shutil.copytree(stage, output)

    print(f"Extracted {len(extracted)} whitelisted MT6572 audio libraries to {output}")
    print("No NVRAM contents, audio calibration, userdata, or logs were copied.")
    if args.include_unverified_backend:
        print("Reference S1R audit: DT_NEEDED closure PASS; ABI strong-symbol link FAIL (10 symbols).")
        print("Diagnostic extraction only: do not flash; BT library is not replaced.")
    print("Pie symbol/structure ABI and device audio remain unverified.")


if __name__ == "__main__":
    main()

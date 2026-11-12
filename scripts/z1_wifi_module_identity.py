#!/usr/bin/env python3
"""Read-only identity gate for the wlan_gen2 module shipped in the Z1 v35 image.

An identity match proves artifact equality, not which source built the artifact.
Use --prepatch-rebuild only for the unmodified, clean rebuild used to establish
source provenance before changing a driver. Never run insmod or write an image.
"""

import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys


EXPECTED_SHA256 = "2cf27905d382fb8ac7e6bd8e000c6db1417c4415b5718086562be11399580e9b"
EXPECTED_VERMAGIC = "7.0.0-rc7-z1+ SMP mod_unload ARMv7 p2v8"
IMAGE_PATH = "/lib/modules/z1-radio/wlan_gen2.ko"


def fingerprint(data):
    """Extract only bounded ELF32 little-endian sections; reject malformed input."""
    if len(data) < 52 or data[:7] != b"\x7fELF\x01\x01\x01":
        raise ValueError("not an ELF32 little-endian module")
    header = struct.unpack_from("<16sHHIIIIIHHHHHH", data)
    if header[1:3] != (1, 40):
        raise ValueError("expected an ARM relocatable ELF object")
    shoff, shentsize, shnum, shstrndx = header[6], header[11], header[12], header[13]
    if shentsize != 40 or not shnum or shstrndx >= shnum:
        raise ValueError("invalid ELF section table")
    if shoff + shnum * shentsize > len(data):
        raise ValueError("truncated ELF section table")
    sections = [struct.unpack_from("<10I", data, shoff + i * 40) for i in range(shnum)]

    def body(section):
        start, size = section[4:6]
        if start + size > len(data):
            raise ValueError("section exceeds module length")
        return data[start:start + size]

    names = body(sections[shstrndx])
    found = {}
    for section in sections:
        name_offset = section[0]
        if name_offset >= len(names) or b"\0" not in names[name_offset:]:
            raise ValueError("invalid ELF section name")
        name = names[name_offset:].split(b"\0", 1)[0].decode("ascii", errors="strict")
        if name in (".modinfo", ".note.gnu.build-id", ".debug_info"):
            found[name] = body(section)
    modinfo = {}
    for field in found.get(".modinfo", b"").split(b"\0"):
        if b"=" in field:
            key, value = field.split(b"=", 1)
            modinfo[key.decode("ascii")] = value.decode("ascii")
    note = found.get(".note.gnu.build-id", b"")
    build_id = None
    if note:
        if len(note) < 12:
            raise ValueError("truncated build-id note")
        namesz, descsz, kind = struct.unpack_from("<III", note)
        desc = 12 + (namesz + 3) // 4 * 4
        if desc + descsz > len(note):
            raise ValueError("truncated build-id descriptor")
        if kind == 3 and note[12:12 + namesz].rstrip(b"\0") == b"GNU":
            build_id = note[desc:desc + descsz].hex()
    digest = hashlib.sha256(data).hexdigest()
    matches = (digest == EXPECTED_SHA256 and modinfo.get("name") == "wlan_gen2"
               and modinfo.get("vermagic", "").strip() == EXPECTED_VERMAGIC)
    return {"size": len(data), "sha256": digest, "name": modinfo.get("name"),
            "vermagic": modinfo.get("vermagic"), "srcversion": modinfo.get("srcversion"),
            "build_id": build_id, "has_dwarf": ".debug_info" in found,
            "matches_v35_module": matches}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--module", action="append", default=[], type=Path)
    parser.add_argument("--image", action="append", default=[], type=Path,
                        help="raw ext4 system image; uses debugfs read-only")
    parser.add_argument("--prepatch-rebuild", type=Path,
                        help="require an unmodified clean rebuild to match v35 byte for byte")
    parser.add_argument("--output", type=Path, help="optional JSON evidence file")
    args = parser.parse_args()
    if not (args.module or args.image or args.prepatch_rebuild):
        parser.error("provide --module, --image or --prepatch-rebuild")
    inputs = [("module", p) for p in args.module] + [("image", p) for p in args.image]
    if args.prepatch_rebuild:
        inputs.append(("prepatch_rebuild", args.prepatch_rebuild))
    records = []
    for kind, path in inputs:
        record = {"kind": kind, "path": str(path.resolve())}
        try:
            if kind == "image":
                if not path.is_file():
                    raise ValueError("system image not found")
                result = subprocess.run(["debugfs", "-R", "cat " + IMAGE_PATH, str(path)],
                                        check=True, capture_output=True, timeout=120)
                data = result.stdout
                record["image_member"] = IMAGE_PATH
            else:
                data = path.read_bytes()
            record.update(fingerprint(data))
        except (OSError, ValueError, struct.error, UnicodeError, subprocess.SubprocessError) as exc:
            record.update(error=str(exc), matches_v35_module=False)
        records.append(record)
    ok = all(r["matches_v35_module"] for r in records)
    report = {"artifact_identity": "MATCH" if ok else "MISMATCH",
              "expected_sha256": EXPECTED_SHA256,
              "source_provenance": "unverified; record source commit, local patches and clean-build command separately",
              "records": records}
    rendered = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        with args.output.open("x") as stream:
            stream.write(rendered)
    sys.stdout.write(rendered)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

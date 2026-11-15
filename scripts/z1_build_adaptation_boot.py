#!/usr/bin/env python3
"""Build a separate Z1 adaptation candidate from a guarded recovery boot.

All inputs are regular host files with explicit SHA256 identities. Existing
cpio records alone may be replaced; storage/init ordering is preserved. The
new kernel and DTB are explicit inputs, and only initrd-end is patched in that
DTB. No device access, mounting, flashing or persistent stage changes.
"""
import argparse
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile

import z1_prepare_recovery_boot as boot


ALLOWED = {
    "hwcomposer.z1.so", "gralloc.z1.so", "z1_audio.primary.mt6572.so",
    "z1_libwifi-hal.so", "z1_libbt-vendor.so", "z1_cst836u.ko",
    "z1_mt6572-afe-pcm.ko", "z1_snd-soc-mt6323.ko", "z1stage_boot.sh",
}


def unique_object(items):
    obj = {}
    for key, value in items:
        boot.require(key not in obj, "duplicate manifest key: " + key)
        obj[key] = value
    return obj


def read_input(item, root):
    boot.require(isinstance(item, dict) and set(item) == {"path", "sha256"},
                 "input must contain path and sha256")
    boot.require(isinstance(item["path"], str) and item["path"], "invalid host path")
    path = Path(item["path"])
    boot.require(".." not in path.parts, "host traversal rejected")
    path = root / path if not path.is_absolute() else path
    boot.require(not any(p.is_symlink() for p in (path, *path.parents)),
                 "host symlink rejected")
    boot.require(path.is_file(), "input is not a regular host file: " + str(path))
    data = path.read_bytes()
    boot.require(boot.sha(data) == item["sha256"], "input SHA256 mismatch: " + str(path))
    return data


def check_arm(data, name):
    boot.require(len(data) >= 52 and data[:6] == b"\x7fELF\x01\x01" and
                 struct.unpack_from("<H", data, 16)[0] ==
                 (3 if name.endswith(".so") else 1) and
                 struct.unpack_from("<H", data, 18)[0] == 40,
                 "replacement is not ELF32 little-endian ARM: " + name)
    if name.endswith(".ko"):
        boot.require(b"vermagic=7.0.0-rc7-z1+" in data,
                     "replacement module vermagic mismatch: " + name)


def build(manifest_path, output):
    manifest_path, output = Path(manifest_path).absolute(), Path(output).absolute()
    boot.require(not any(p.is_symlink() for p in (manifest_path, *manifest_path.parents)),
                 "manifest symlink rejected")
    m = json.loads(manifest_path.read_text(), object_pairs_hook=unique_object)
    boot.require(isinstance(m, dict) and set(m) == {
        "version", "base", "zimage", "dtb", "replacements"} and
        type(m["version"]) is int and m["version"] == 1, "invalid manifest schema")
    root = manifest_path.parent
    raw, zimage, dtb = (read_input(m[k], root) for k in ("base", "zimage", "dtb"))
    header, kh, old_zimage, old_dtb, rootfs = boot.split_boot(raw)
    boot.require(len(zimage) >= 0x30 and zimage[0x24:0x28] == bytes.fromhex("18286f01")
                 and struct.unpack_from("<I", zimage, 0x2c)[0] == len(zimage),
                 "invalid standalone zImage length or magic")
    boot.require(len(dtb) >= 40 and dtb[:4] == bytes.fromhex("d00dfeed") and
                 struct.unpack_from(">I", dtb, 4)[0] == len(dtb), "invalid standalone DTB")
    body, records = boot.cpio(rootfs)
    old = boot.contents(body, records)
    boot.verify_rc(old["init.z1.rc"].decode())
    replacements = m["replacements"]
    boot.require(isinstance(replacements, list) and 0 < len(replacements) <= len(ALLOWED),
                 "need explicit cpio replacements")
    payloads = {}
    for entry in replacements:
        boot.require(isinstance(entry, dict) and set(entry) == {
            "name", "source", "original_sha256"}, "invalid replacement schema")
        name = entry["name"]
        boot.require(isinstance(name, str) and name in ALLOWED and name in old and
                     name not in payloads, "disallowed, missing or duplicate cpio record")
        boot.require(boot.sha(old[name]) == entry["original_sha256"],
                     "old cpio SHA256 mismatch: " + name)
        data = read_input(entry["source"], root)
        if name.endswith((".so", ".ko")):
            check_arm(data, name)
        if name == "z1stage_boot.sh":
            for token in (b"--storage-only", b"--skip-storage", b"sys.z1.storage_ready"):
                boot.require(token in data, "stage script lacks storage gate")
        payloads[name] = data
    updated = boot.edit_rootfs(rootfs, payloads)
    boot.require(not os.path.lexists(output) and output.parent.is_dir(),
                 "output must be a new directory below an existing parent")
    boot.require(not any(p.is_symlink() for p in (output, *output.parents)),
                 "output symlink rejected")
    with tempfile.TemporaryDirectory(prefix=".z1-adaptation-", dir=output.parent) as td:
        work = Path(td)
        previous, current = work / "baseline.dtb", work / "appended.dtb"
        previous.write_bytes(old_dtb)
        current.write_bytes(dtb)
        for node, prop, kind in (("/", "compatible", "s"), ("/chosen", "bootargs", "s"),
                                  ("/chosen", "linux,initrd-start", "x")):
            boot.require(boot.dt_read(previous, node, prop, kind) ==
                         boot.dt_read(current, node, prop, kind),
                         "changed boot contract: " + node + "/" + prop)
        start = int(boot.dt_read(current, "/chosen", "linux,initrd-start"), 16)
        boot.require(start == 0x84100000, "unexpected LK initrd start")
        end = start + len(updated) - 512
        subprocess.run(["fdtput", "-t", "x", str(current), "/chosen",
                        "linux,initrd-end", f"{end:x}"], check=True)
        patched_dtb = current.read_bytes()
        image = boot.make_image(header, kh, zimage, patched_dtb, updated)
        _, _, checked_zimage, checked_dtb, checked_rootfs = boot.split_boot(image)
        boot.require((checked_zimage, checked_dtb, checked_rootfs) ==
                     (zimage, patched_dtb, updated), "packed payload mismatch")
        new_body, new_records = boot.cpio(checked_rootfs)
        new = boot.contents(new_body, new_records)
        boot.require(list(new) == list(old), "cpio record set/order changed")
        for before, after in zip(records, new_records):
            name = before["name"]
            if name in payloads:
                boot.require(new[name] == payloads[name], "replacement readback mismatch")
                boot.require(before["fields"][:6] == after["fields"][:6],
                             "cpio metadata changed")
            else:
                boot.require(body[before["pos"]:before["file_end"]] ==
                             new_body[after["pos"]:after["file_end"]],
                             "untouched cpio record changed: " + name)
        boot.verify_rc(new["init.z1.rc"].decode())
        for path in (previous, current):
            result = subprocess.run(["dtc", "-I", "dtb", "-O", "dts", str(path)],
                                    check=True, capture_output=True)
            path.with_suffix(".dts").write_bytes(result.stdout)
        report = {
            "status": "HOST_VERIFIED_NOT_FLASHED", "inputs": m,
            "boot_sha256": boot.sha(image), "boot_bytes": len(image),
            "zimage_sha256": boot.sha(zimage), "previous_zimage_sha256": boot.sha(old_zimage),
            "dtb_sha256": boot.sha(patched_dtb), "rootfs_sha256": boot.sha(updated),
            "initrd_start": hex(start), "initrd_end": hex(end),
            "rootfs_body_bytes": len(updated) - 512,
            "cpio_replacements": {name: {"sha256": boot.sha(data), "bytes": len(data)}
                                  for name, data in payloads.items()},
            "preserved_init_storage_order": True,
            "persistent_stage_warning": "Existing /data/local/tmp/z1stage/active overrides and kill switches retain precedence; verify runtime hashes before testing.",
            "builder_sha256": boot.sha(Path(__file__).read_bytes()),
        }
        (work / "boot.img").write_bytes(image)
        (work / "rootfs.bin").write_bytes(updated)
        (work / "manifest.json").write_text(json.dumps(report, indent=2) + "\n")
        (work / "SHA256SUMS").write_text("".join(
            f"{boot.sha(p.read_bytes())}  {p.name}\n"
            for p in sorted(work.iterdir()) if p.is_file()))
        os.rename(work, output)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.manifest, args.output_dir), indent=2))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Add only a default wallpaper to a copy of the original R9 raw system image.

The original /build.prop inode and all other files are left in place. The
system-as-root ramdisk startup adds a matching restorecon_recursive call for
the new PNG, so the unlabelled file is corrected at first boot using the
existing /plat_file_contexts policy, not a guessed host-side label.
"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent.parent
EXPECTED = "4289d6c1ed9c155da197a96c06927ffd2dc51d3f7fd0123deed78e6fd5d33a33"
SIZE = 943718400


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(image, command, write=False):
    r = subprocess.run(["debugfs"] + (["-w"] if write else []) + ["-R", command, str(image)], capture_output=True)
    text = (r.stdout + r.stderr).decode(errors="replace")
    if command.startswith("dump "):
        return b""
    if r.returncode or re.search(r"File not found|Usage:|No space left|Could not allocate|Invalid argument", text, re.I):
        raise RuntimeError(f"debugfs failed for {command}: {text[-300:]}")
    return text


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("base", type=Path)
    p.add_argument("output_dir", type=Path)
    p.add_argument("--expected-sha256", default=EXPECTED)
    p.add_argument("--ramdisk-contexts", type=Path, help="plat_file_contexts extracted from the paired R9 boot ramdisk")
    p.add_argument("--driver-probe-gz", type=Path, help="gzip-compressed static ARM32 i2cprobe, added under /system/etc/z1-driver-evidence")
    p.add_argument("--driver-module-gz", type=Path, help="gzip-compressed R9-matching edt-ft5x06.ko, never auto-loaded")
    args = p.parse_args()
    if bool(args.driver_probe_gz) != bool(args.driver_module_gz):
        raise ValueError("driver probe and module attachments must be supplied together")
    driver_inputs = []
    if args.driver_probe_gz:
        for path, name, expected_magic in [(args.driver_probe_gz, "i2cprobe.gz", b"\x7fELF"), (args.driver_module_gz, "edt-ft5x06.ko.gz", b"\x7fELF")]:
            if not path.is_file():
                raise ValueError("missing compressed driver attachment: " + str(path))
            data = gzip.decompress(path.read_bytes())
            if data[:4] != expected_magic or data[4] != 1 or data[18:20] != b"\x28\x00":
                raise ValueError("driver attachment must be ARM32 ELF")
            if name.startswith("edt") and b"vermagic=7.0.0-rc7-z1+" not in data:
                raise ValueError("touch module vermagic mismatch")
            driver_inputs.append((path, name, data))
    if args.output_dir.exists() or sha(args.base) != args.expected_sha256 or args.base.stat().st_size != SIZE:
        raise ValueError("input is not pristine R9 or output directory already exists")
    args.output_dir.mkdir(parents=True)
    image = args.output_dir / "system.img"
    shutil.copyfile(args.base, image)
    original_props = run(image, "cat /build.prop").splitlines()
    if any(line.startswith("ro.config.wallpaper=") for line in original_props):
        raise ValueError("base already has a wallpaper property")
    if not args.ramdisk_contexts or not args.ramdisk_contexts.is_file():
        raise ValueError("the paired boot's plat_file_contexts is required")
    contexts = args.ramdisk_contexts.read_text().splitlines()
    if not any(re.match(r"/system\(/\.\*\)\?\s+u:object_r:system_file:s0", line) for line in contexts):
        raise ValueError("R9 policy does not provide the expected /system/etc label")
    try:
        run(image, "stat /etc/z1_wallpaper.png")
    except RuntimeError as exc:
        if "File not found" not in str(exc):
            raise
    else:
        raise ValueError("target already exists in base")
    png = ROOT / "graphics/wallpaper/z1_default.png"
    run(image, f"write {png} /etc/z1_wallpaper.png", True)
    if args.driver_probe_gz:
        run(image, "mkdir /etc/z1-driver-evidence", True)
        if "Type: directory" not in run(image, "stat /etc/z1-driver-evidence"):
            raise ValueError("driver attachment directory was not created")
    with tempfile.TemporaryDirectory(prefix="z1-wallpaper-verify-") as temp:
        for path, name, data in driver_inputs:
            run(image, f"write {path} /etc/z1-driver-evidence/{name}", True)
            run(image, f"set_inode_field /etc/z1-driver-evidence/{name} mode 0100644", True)
            verify = Path(temp) / name
            run(image, f"dump /etc/z1-driver-evidence/{name} {verify}")
            if gzip.decompress(verify.read_bytes()) != data:
                raise ValueError("compressed driver readback differs: " + name)
        extracted = Path(temp) / "wallpaper.png"
        run(image, f"dump /etc/z1_wallpaper.png {extracted}")
        if extracted.read_bytes() != png.read_bytes():
            raise ValueError("wallpaper readback differs")
        props = original_props + ["ro.config.wallpaper=/system/etc/z1_wallpaper.png"]
        updated = Path(temp) / "build.prop"
        updated.write_text("\n".join(props) + "\n")
        run(image, "rm /build.prop", True)
        run(image, f"write {updated} /build.prop", True)
        # Preserve the original build.prop mode, rather than applying a blanket 0644.
        run(image, "set_inode_field /build.prop mode 0100600", True)
        readback = Path(temp) / "build.prop"
        run(image, f"dump /build.prop {readback}")
        if readback.read_text().splitlines() != props:
            raise ValueError("build.prop content differs")
        run(image, "set_inode_field /etc/z1_wallpaper.png mode 0100644", True)
        for path, mode in [("/etc/z1_wallpaper.png", "0644"), ("/build.prop", "0600")]:
            text = run(image, "stat " + path)
            if "Type: regular" not in text or f"Mode:  {mode}" not in text:
                raise ValueError("incorrect regular file mode at " + path)
    check = subprocess.run(["e2fsck", "-fn", str(image)], capture_output=True, text=True)
    if check.returncode:
        raise RuntimeError("e2fsck failed: " + (check.stdout + check.stderr)[-500:])
    manifest = {
        "status": "HOST_PACKAGE_PASS_NOT_DEVICE_VERIFIED",
        "base_sha256": EXPECTED,
        "output_sha256": sha(image),
        "bytes": SIZE,
        "changes": ["/system/etc/z1_wallpaper.png", "/system/build.prop"] + (["/system/etc/z1-driver-evidence/i2cprobe.gz", "/system/etc/z1-driver-evidence/edt-ft5x06.ko.gz"] if driver_inputs else []),
        "property": "ro.config.wallpaper=/system/etc/z1_wallpaper.png",
        "selinux": "Paired boot ramdisk must run restorecon_recursive /system/etc after mount; no policy or /data changed.",
        "notes": "Factory default only; existing /data wallpaper takes precedence. No device test. Driver attachments require manual unpacking; no probe, module insertion, GPIO or charging control runs automatically.",
    }
    (args.output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()

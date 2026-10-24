#!/usr/bin/env python3
"""Add only a default wallpaper to a copy of the original R9 raw system image.

The original /build.prop inode and all other files are left in place. The
system-as-root ramdisk startup adds a matching restorecon_recursive call for
the new PNG, so the unlabelled file is corrected at first boot using the
existing /plat_file_contexts policy, not a guessed host-side label.
"""
import argparse
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
    args = p.parse_args()
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
    with tempfile.TemporaryDirectory(prefix="z1-wallpaper-verify-") as temp:
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
        "changes": ["/system/etc/z1_wallpaper.png", "/system/build.prop"],
        "property": "ro.config.wallpaper=/system/etc/z1_wallpaper.png",
        "selinux": "Paired boot ramdisk must run restorecon_recursive /system/etc after mount; no policy or /data changed.",
        "notes": "Factory default only; existing /data wallpaper takes precedence. No device test.",
    }
    (args.output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()

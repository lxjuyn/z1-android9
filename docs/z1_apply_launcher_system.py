#!/usr/bin/env python3
"""Install Trebuchet (com.android.launcher3) into a *copy* of the Z1 Android-9 system image.

Why a copy: the base image is the known-good R9/A9 deliverable. Every change is made
with debugfs on the copy, then read back and hash-verified, so a half-written inode can
never be mistaken for a successful run.

What it does (each step verified by readback):
  1. /priv-app/Trebuchet/Trebuchet.apk      (mode 0644, dir 0755)  -- privileged because
     the app requests MANAGE_ACTIVITY_STACKS / STATUS_BAR, which only count for priv-app.
  2. optional: remove /app/Home/Home.apk    -- otherwise two HOME activities exist and the
     first boot shows a "Home app" chooser instead of going straight to Trebuchet.
  3. optional: replace /etc/z1_wallpaper.png -- ro.config.wallpaper points at it; the
     shipped one is a single flat colour (#1c2630), i.e. it looks exactly like "no wallpaper".

No permission XML is touched: /etc/permissions/privapp-permissions-platform.xml already
grants com.android.launcher3 BIND_APPWIDGET + CONTROL_REMOTE_APP_TRANSITION_ANIMATIONS,
and privapp-permissions-lineage.xml already grants MANAGE_ACTIVITY_STACKS + STATUS_BAR.

Usage:
  python3 z1_apply_launcher_system.py <base_system.img> <out_dir> \
      [--apk TrebuchetQuickStep.apk] [--wallpaper z1_wallpaper_240x320.png] [--keep-home]
"""
import argparse
import hashlib
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TREBUCHET_SHA = "98cab8c5377b8bb047ad5804a2e5f9a1c4c998dd1324b482398c10de361b9475"
WALLPAPER_SHA = "a1a26036dd1995d75783e068f7b28084f3c56ec7dc3bef56887f6aeac6a3e374"


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def run(image, command, write=False):
    r = subprocess.run(["debugfs"] + (["-w"] if write else []) + ["-R", command, str(image)],
                       capture_output=True)
    text = (r.stdout + r.stderr).decode(errors="replace")
    if r.returncode or re.search(r"File not found|Usage:|No space left|Could not allocate|Invalid argument",
                                 text, re.I):
        raise RuntimeError(f"debugfs failed for `{command}`: {text.strip()[-300:]}")
    return text


def exists(image, path):
    try:
        run(image, f"stat {path}")
        return True
    except RuntimeError as exc:
        if "File not found" in str(exc):
            return False
        raise


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("base", type=Path)
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("--apk", type=Path, default=HERE / "TrebuchetQuickStep.apk")
    ap.add_argument("--wallpaper", type=Path, default=HERE / "z1_wallpaper_240x320.png")
    ap.add_argument("--keep-home", action="store_true", help="keep com.example.android.home installed")
    args = ap.parse_args()

    apk = args.apk.read_bytes()
    if sha_bytes(apk) != TREBUCHET_SHA:
        raise SystemExit(f"refusing: {args.apk} is not the verified TrebuchetQuickStep.apk")
    wp = args.wallpaper.read_bytes() if args.wallpaper else None
    if wp is not None and sha_bytes(wp) != WALLPAPER_SHA:
        raise SystemExit(f"refusing: {args.wallpaper} is not the verified 240x320 wallpaper")
    if args.out_dir.exists():
        raise SystemExit(f"refusing: {args.out_dir} already exists")
    if not args.base.is_file():
        raise SystemExit(f"refusing: base image {args.base} not found")

    args.out_dir.mkdir(parents=True)
    image = args.out_dir / "system.img"
    print(f"copying {args.base} -> {image} ...")
    shutil.copyfile(args.base, image)

    # 1. Trebuchet as a privileged app
    if not exists(image, "/priv-app"):
        raise SystemExit("refusing: base has no /priv-app")
    run(image, "mkdir /priv-app/Trebuchet", True)
    if "Type: directory" not in run(image, "stat /priv-app/Trebuchet"):
        raise SystemExit("mkdir /priv-app/Trebuchet did not produce a directory")
    with tempfile.TemporaryDirectory(prefix="z1-launcher-") as tmp:
        src = Path(tmp) / "Trebuchet.apk"
        src.write_bytes(apk)
        run(image, f"write {src} /priv-app/Trebuchet/Trebuchet.apk", True)
    run(image, "set_inode_field /priv-app/Trebuchet mode 040755", True)
    run(image, "set_inode_field /priv-app/Trebuchet/Trebuchet.apk mode 0100644", True)
    with tempfile.TemporaryDirectory(prefix="z1-launcher-verify-") as tmp:
        back = Path(tmp) / "Trebuchet.apk"
        run(image, f"dump /priv-app/Trebuchet/Trebuchet.apk {back}")
        if sha_bytes(back.read_bytes()) != TREBUCHET_SHA:
            raise SystemExit("readback differs: /priv-app/Trebuchet/Trebuchet.apk")
    print("  ok  /priv-app/Trebuchet/Trebuchet.apk  0644 root:root  sha256 verified")

    # 2. drop the AOSP Home sample so exactly one HOME activity remains
    if not args.keep_home:
        if exists(image, "/app/Home/Home.apk"):
            run(image, "rm /app/Home/Home.apk", True)
            if exists(image, "/app/Home/Home.apk"):
                raise SystemExit("could not remove /app/Home/Home.apk")
            print("  ok  removed /app/Home/Home.apk (com.example.android.home)")
        else:
            print("  --  /app/Home/Home.apk already absent")

    # 3. replace the flat-colour default wallpaper
    if wp is not None:
        if exists(image, "/etc/z1_wallpaper.png"):
            run(image, "rm /etc/z1_wallpaper.png", True)
        with tempfile.TemporaryDirectory(prefix="z1-wallpaper-") as tmp:
            src = Path(tmp) / "z1_wallpaper.png"
            src.write_bytes(wp)
            run(image, f"write {src} /etc/z1_wallpaper.png", True)
        run(image, "set_inode_field /etc/z1_wallpaper.png mode 0100644", True)
        with tempfile.TemporaryDirectory(prefix="z1-wallpaper-verify-") as tmp:
            back = Path(tmp) / "z1_wallpaper.png"
            run(image, f"dump /etc/z1_wallpaper.png {back}")
            if sha_bytes(back.read_bytes()) != WALLPAPER_SHA:
                raise SystemExit("readback differs: /etc/z1_wallpaper.png")
        print("  ok  /etc/z1_wallpaper.png replaced (240x320 RGB, sha256 verified)")

    print()
    print("done ->", image)
    print("next: flash this image in place of the current system partition, then on-device checks:")
    print("  cmd package resolve-activity --brief -c android.intent.category.HOME -a android.intent.action.MAIN")
    print("     expect com.android.launcher3/.lineage.LineageLauncher")
    print("  dumpsys window | grep mCurrentFocus")
    print("  ls -l /data/system/users/0/ | grep wallpaper   # a user wallpaper here overrides ro.config.wallpaper")


if __name__ == "__main__":
    main()

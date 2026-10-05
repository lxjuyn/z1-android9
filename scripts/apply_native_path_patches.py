#!/usr/bin/env python3
"""Apply or verify the pinned native __FILE__ path generator patches."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
EMPTY_SHA = hashlib.sha256(b"").hexdigest()


def sha256(path):
    if not path.exists():
        return EMPTY_SHA
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path,
                        help="LineageOS 16 source root")
    parser.add_argument("--check", action="store_true",
                        help="verify patches without modifying the source")
    args = parser.parse_args()
    source = args.source.resolve()
    manifest = json.loads(
        (ROOT / "native_path_patches/manifest.json").read_text())
    failed = False

    for item in manifest:
        target = source / item["file"]
        patch = ROOT / "native_path_patches" / item["patch"]
        current = sha256(target)
        if current == item["result_sha256"]:
            print("OK already applied: {}".format(item["file"]))
            continue
        if args.check or current != item["base_sha256"]:
            expected = "result" if args.check else "base/result"
            print("HASH MISMATCH: {} current={} expected={}".format(
                item["file"], current, expected), file=sys.stderr)
            failed = True
            continue

        command = ["patch", "--fuzz=0", "-p1", "--directory", str(source),
                   "--input", str(patch)]
        dry_run = subprocess.run(
            command[:1] + ["--dry-run"] + command[1:],
            text=True, capture_output=True)
        if dry_run.returncode:
            detail = (dry_run.stderr or dry_run.stdout).strip()
            print("PATCH DOES NOT APPLY: {}: {}".format(
                item["patch"], detail), file=sys.stderr)
            failed = True
            continue

        subprocess.run(command, check=True, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, text=True)
        actual = sha256(target)
        if actual != item["result_sha256"]:
            print("RESULT HASH MISMATCH: {} got={}".format(
                item["file"], actual), file=sys.stderr)
            failed = True
        else:
            print("APPLIED: {}".format(item["patch"]))

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

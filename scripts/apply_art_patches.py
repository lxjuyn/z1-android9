#!/usr/bin/env python3
"""Apply or verify the Z1 ART/OAT patch series with strict source hashes."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path,
                        help="LineageOS 16 source checkout")
    parser.add_argument("--check", action="store_true",
                        help="verify result hashes without modifying source")
    args = parser.parse_args()
    source = args.source.resolve()
    manifest = json.loads((ROOT / "art_patches/manifest.json").read_text())
    failed = False

    for item in manifest["patches"]:
        targets = item["targets"]
        states = []
        for target in targets:
            actual = sha(source / target["file"])
            if actual == target["result_sha256"]:
                states.append("result")
            elif actual == target["base_sha256"]:
                states.append("base")
            else:
                states.append("mismatch")

        if all(state == "result" for state in states):
            print("OK already applied: " + item["patch"])
            continue
        if args.check or not all(state == "base" for state in states):
            print("HASH MISMATCH: " + item["patch"], file=sys.stderr)
            failed = True
            continue

        patch = ROOT / "art_patches" / item["patch"]
        command = ["patch", "--fuzz=0", "-p1", "--directory", str(source), "--input", str(patch)]
        dry_run = subprocess.run(command[:1] + ["--dry-run"] + command[1:],
                                 text=True, capture_output=True)
        if dry_run.returncode:
            print("PATCH DOES NOT APPLY: " + item["patch"], file=sys.stderr)
            failed = True
            continue
        applied = subprocess.run(command, text=True, capture_output=True)
        if applied.returncode:
            print("PATCH FAILED: " + item["patch"], file=sys.stderr)
            failed = True
            continue

        results_match = all(
            sha(source / target["file"]) == target["result_sha256"]
            for target in targets
        )
        if not results_match:
            print("RESULT HASH MISMATCH: " + item["patch"], file=sys.stderr)
            failed = True
        else:
            print("APPLIED: " + item["patch"])

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

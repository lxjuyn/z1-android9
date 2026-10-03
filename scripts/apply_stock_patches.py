#!/usr/bin/env python3
"""Apply or verify the Z1 stock patch series with strict source hashes."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
EMPTY_SHA = hashlib.sha256(b"").hexdigest()

def sha(path):
    if not path.exists():
        return EMPTY_SHA
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, type=Path, help="LOS16 source root")
    ap.add_argument("--check", action="store_true", help="verify all result hashes without modifying source")
    args = ap.parse_args()
    source = args.source.resolve()
    entries = json.loads((ROOT / "stock_patches/manifest.json").read_text())
    failed = False
    for item in entries:
        target = source / item["file"]
        patch = ROOT / "stock_patches" / item["patch"]
        current = sha(target)
        result = item["result_sha256"]
        base = item.get("base_sha256", EMPTY_SHA)
        if current == result:
            print(f"OK already applied: {item['file']}")
            continue
        if args.check or current != base:
            print(f"HASH MISMATCH: {item['file']} current={current} expected={'result' if args.check else 'base/result'}", file=sys.stderr)
            failed = True
            continue
        check = subprocess.run(["git", "apply", "--check", str(patch)], cwd=source)
        if check.returncode:
            print(f"PATCH DOES NOT APPLY: {item['patch']}", file=sys.stderr)
            failed = True
            continue
        subprocess.run(["git", "apply", str(patch)], cwd=source, check=True)
        actual = sha(target)
        if actual != result:
            print(f"RESULT HASH MISMATCH: {item['file']} got={actual} expected={result}", file=sys.stderr)
            failed = True
        else:
            print(f"APPLIED: {item['file']}")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())

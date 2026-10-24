#!/usr/bin/env python3
"""Reject secrets and private material in a reviewable git staging set.

This reads local git index/blob data only. It is not a privacy guarantee or a
substitute for reviewing the staged diff. Binary payloads must be reviewed by
the repository's source manifest before release.
"""
import argparse
import hashlib
from pathlib import Path
import re
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staged", action="store_true")
    parser.add_argument("--tree", default="")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--exclude", action="append", default=[],
                        help="additional repository-relative path regex")
    args = parser.parse_args()
    if bool(args.staged) == bool(args.tree):
        parser.error("choose exactly one of --staged or --tree")
    if args.staged:
        names = subprocess.check_output(["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"], cwd=args.root)
        blobs = {path: subprocess.check_output(["git", "show", f":{path}"], cwd=args.root)
                 for path in (p.decode() for p in names.split(b"\0") if p)}
        scope = "staged changes"
    else:
        names = subprocess.check_output(["git", "ls-tree", "-r", "-z", "--name-only", args.tree], cwd=args.root)
        blobs = {path: subprocess.check_output(["git", "show", f"{args.tree}:{path}"], cwd=args.root)
                 for path in (p.decode() for p in names.split(b"\0") if p)}
        scope = f"tree {args.tree}"
    patterns = {
        "github-token": re.compile(rb"github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{20,}"),
        "private-key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
        "private-url-credential": re.compile(rb"https?://[^\s/:]+:[^\s@]+@"),
        "assigned-secret": re.compile(rb"(?i)(?:api[_-]?key|access[_-]?token|client[_-]?secret|password)\s*[:=]\s*[\"'][^\"'\r\n]{8,}[\"']"),
    }
    excluded = [re.compile(p) for p in args.exclude]
    findings = []
    for path, data in blobs.items():
        if any(p.search(path) for p in excluded):
            continue
        for kind, pattern in patterns.items():
            if pattern.search(data):
                findings.append(f"{path}: potential {kind} (content not displayed)")
    if findings:
        print("SECURITY REVIEW FAILED for " + scope, file=sys.stderr)
        print("\n".join(findings), file=sys.stderr)
        print("Remove the secret from content/history, rotate any exposed credential, and rerun the review.", file=sys.stderr)
        return 1
    print(f"Security review passed for {scope}: {len(blobs)} text/binary blobs scanned; no configured secret pattern matched.")
    print(f"Reviewed payload bytes: {sum(map(len, blobs.values()))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

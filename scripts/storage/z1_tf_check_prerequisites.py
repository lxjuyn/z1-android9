#!/usr/bin/env python3
"""Offline evidence completeness check; never edits DTS/fstab or accesses a board.

Use --manifest FILE --evidence-root DIRECTORY. See the adjacent example JSON.
All verified entries need Z1 provenance and SHA256-pinned regular evidence files
inside the evidence root. Completeness is NOT proof of safe wiring or readiness
to mount/flash. This checks supplied evidence, not its technical truth.
Exit 0: complete checklist; 1: missing/unverified evidence; 2: invalid input.
"""
import argparse
import hashlib
import json
from pathlib import Path
import stat
import sys

REQUIRED = {
    'tf_slot': 'Z1 TF slot existence and board identification',
    'host_mapping': 'Z1 TF socket to controller mapping, independent of mmc numbering',
    'pinmux': 'Z1 CLK/CMD/DAT wiring and pinmux; no copied board pin numbers',
    'card_detect': 'Z1 CD wiring/polarity or justified polling/no-CD design',
    'vmmc': 'Z1 card power rail, voltage, polarity and power sequence',
    'vqmmc': 'Z1 signal voltage and supported voltage switching policy',
    'kernel_support': 'Actual kernel MMC host and needed filesystem configuration',
    'dts': 'Reviewed Z1 removable host, pins, CD and supply description',
    'fstab': 'Reviewed Z1 voldmanaged path tied to the verified host',
    'vold': 'Required vold/filesystem support and volume policy evidence',
}
MAX_BYTES = 8 * 1024 * 1024


def load_regular(path):
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError('not a regular file')
    if path.stat().st_size > MAX_BYTES:
        raise ValueError('file exceeds 8 MiB limit')
    with path.open('rb') as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError('file exceeds 8 MiB limit')
    return data


def evidence_error(entry, root):
    if not isinstance(entry, dict):
        return 'invalid evidence record'
    name = entry.get('path')
    digest = entry.get('sha256')
    if not isinstance(name, str) or not name or Path(name).is_absolute() or '..' in Path(name).parts:
        return 'evidence path must be relative and confined to evidence root'
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
        return 'missing or invalid sha256'
    try:
        path = (root / name).resolve(strict=True)
        path.relative_to(root)
        if hashlib.sha256(load_regular(path)).hexdigest() != digest:
            return 'sha256 mismatch: ' + name
    except (OSError, ValueError, RuntimeError):
        return 'unreadable, non-regular or escaping evidence: ' + name
    return None


def check(manifest, root):
    if (not isinstance(manifest, dict) or type(manifest.get('schema_version')) is not int
            or manifest['schema_version'] != 1):
        raise ValueError('expected schema_version 1 object')
    if manifest.get('board') != 'Z1':
        raise ValueError('manifest must identify board Z1')
    facts = manifest.get('facts')
    if not isinstance(facts, dict):
        raise ValueError('facts must be an object')
    unknown_keys = sorted(set(facts) - set(REQUIRED))
    if unknown_keys:
        raise ValueError('unknown fact keys: ' + ', '.join(unknown_keys))
    missing = []
    verified = []
    for key, purpose in REQUIRED.items():
        fact = facts.get(key)
        issues = []
        if not isinstance(fact, dict):
            issues.append('missing entry')
        else:
            if fact.get('status') != 'verified':
                issues.append('status is not verified')
            if fact.get('board') != 'Z1':
                issues.append('missing Z1-specific provenance')
            if not isinstance(fact.get('value'), str) or not fact['value'].strip():
                issues.append('missing documented finding')
            refs = fact.get('evidence')
            if not isinstance(refs, list) or not refs:
                issues.append('missing evidence references')
            else:
                for ref in refs:
                    error = evidence_error(ref, root)
                    if error:
                        issues.append(error)
        if issues:
            missing.append({'item': key, 'required': purpose, 'issues': issues})
        else:
            verified.append(key)
    return {
        'schema_version': 1, 'board': 'Z1',
        'status': 'incomplete' if missing else 'documented',
        'missing': missing, 'verified': verified,
        'hardware_validated': False, 'ready_to_modify': False,
        'note': 'Offline evidence completeness only; no DTS/fstab changes or hardware checks.',
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--evidence-root', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        root = args.evidence_root.resolve(strict=True)
        if not root.is_dir() or root == Path('/'):
            raise ValueError('evidence root must be a dedicated offline directory')
        manifest_path = args.manifest.resolve(strict=True)
        manifest_path.relative_to(root)
        manifest = json.loads(load_regular(manifest_path))
        result = check(manifest, root)
    except (OSError, ValueError, RuntimeError, UnicodeError) as exc:
        print(json.dumps({'status': 'invalid_input', 'error': str(exc)}))
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 1 if result['missing'] else 0


if __name__ == '__main__':
    sys.exit(main())

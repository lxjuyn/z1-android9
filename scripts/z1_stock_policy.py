#!/usr/bin/env python3
"""Build a diagnostic-only Pie policy26 for Z1's unchanged stock kernel.

This never edits Android sources, never loads policy, and never changes init.
Unknown kernel permissions are allowed and every domain is permissive. These
intentional losses of enforcement must not be shipped as a security solution.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import sys


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(argv, log):
    proc = subprocess.run([str(x) for x in argv], stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, timeout=60)
    log.write_bytes(proc.stdout)
    if proc.returncode:
        raise RuntimeError(f"command exited {proc.returncode}; see {log}")
    return proc.stdout.decode(errors="replace")


def header(path):
    data = Path(path).read_bytes()
    magic, length = struct.unpack_from('<II', data)
    if magic != 0xF97CFF8C or length != 8 or data[8:16] != b'SE Linux':
        raise RuntimeError('invalid SELinux binary header')
    version, config, symnum, oconnum = struct.unpack_from('<4I', data, 16)
    return dict(bytes=len(data), sha256=sha(path), policy_version=version,
                config=config, symbol_tables=symnum, object_contexts=oconnum)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--checkpolicy', type=Path, required=True)
    parser.add_argument('--analyze', type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    dest = args.output_dir
    original = args.input.read_text()
    (dest / 'input.policy.conf').write_text(original)
    # Remove comment text before statement matching. policy.conf has ordinary
    # SELinux identifiers/context paths, not strings containing literal '#'.
    clean = re.sub(r'(?m)#.*$', '', original)
    removed = {}
    for kind in ('allowxperm', 'auditallowxperm', 'dontauditxperm', 'neverallowxperm'):
        pattern = re.compile(r'(?m)^\s*' + kind + r'\b[^;]*;')
        matches = pattern.findall(clean)
        removed[kind] = len(matches)
        (dest / (kind + '.removed')).write_text('\n'.join(matches))
        clean = pattern.sub('\n', clean)
    # Stock 3.4 has neither the extended-socket-class policycap nor its class
    # remapping. Keep the historical network_peer_controls/open_perms caps.
    cap = re.compile(r'(?m)^\s*policycap\s+extended_socket_class\s*;')
    removed['extended_socket_class'] = len(cap.findall(clean))
    clean = cap.sub('\n', clean)
    baseconf = dest / 'base.policy.conf'
    baseconf.write_text(clean)
    base = dest / 'base.policy26.bin'
    run([args.checkpolicy, '-M', '-c', '26', '-U', 'allow', '-o', base, baseconf],
        dest / 'compile_base.log')
    domains = set(run([args.analyze, base, 'attribute', 'domain'],
                      dest / 'domains.log').split())
    if not domains or any(not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', x) for x in domains):
        raise RuntimeError('invalid or empty domain enumeration')
    if not {'init', 'ueventd', 'adbd', 'zygote', 'system_server'}.issubset(domains):
        raise RuntimeError('essential Pie domains absent')
    existing = set(re.findall(r'(?m)^\s*permissive\s+([A-Za-z_][A-Za-z_0-9]*)\s*;', clean))
    finalconf = dest / 'diagnostic.policy.conf'
    # checkpolicy requires TE/RBAC declarations before user/context sections.
    user_start = re.search(r'(?m)^\s*user\s+', clean)
    if user_start is None:
        raise RuntimeError('policy lacks a user section')
    extra = '\n# DIAGNOSTIC ONLY: every domain is permissive.\n' + ''.join(
        'permissive ' + x + ';\n' for x in sorted(domains - existing))
    finalconf.write_text(clean[:user_start.start()] + extra + clean[user_start.start():])
    final = dest / 'sepolicy'
    run([args.checkpolicy, '-M', '-c', '26', '-U', 'allow', '-o', final, finalconf],
        dest / 'compile_final.log')
    info = header(final)
    if info['policy_version'] != 26 or not info['config'] & 1 or not info['config'] & 4:
        raise RuntimeError('policy is not v26 MLS + allow_unknown')
    run([args.checkpolicy, '-b', '-M', '-c', '26', '-U', 'allow', '-o',
         dest / 'roundtrip.policy26.bin', final], dest / 'roundtrip.log')
    permissive = set(run([args.analyze, final, 'permissive'], dest / 'permissive.log').split())
    if not domains.issubset(permissive):
        raise RuntimeError('not all domains are permissive')
    manifest = {'status': 'HOST_COMPILE_AND_PARSE_PASS_NOT_KERNEL_LOADED',
                'input': str(args.input), 'input_sha256': sha(args.input),
                'tools': {str(x): sha(x) for x in (args.checkpolicy, args.analyze)},
                'removed_statements': removed, 'domain_count': len(domains),
                'permissive_count': len(permissive), 'final': info,
                'all_domains_permissive': True, 'allow_unknown': True,
                'risk': 'Diagnostic only; xperm checks removed, every domain permissive, '
                        'unknown class/permission access allowed. No enforcement claim.',
                'kernel_loaded': False}
    (dest / 'MANIFEST.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

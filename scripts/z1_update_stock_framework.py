#!/usr/bin/env python3
"""Replace compiled services artifacts in an offline, regular ext4 candidate.

Never use on a block device or the sole rollback image. Preserve file ownership,
mode and the complete SELinux label; save old payloads before replacement.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import stat
import subprocess
import tempfile


def run(image, request, write=False):
    p = subprocess.run(['debugfs'] + (['-w'] if write else []) +
                       ['-R', request, str(image)], capture_output=True, check=True)
    return p.stdout


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda: f.read(1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()


def replace(image, tree, backup, names=None):
    if not stat.S_ISREG(image.stat().st_mode):
        raise ValueError('image must be a regular file')
    if backup.exists():
        raise ValueError('backup must be new')
    backup.mkdir(parents=True)
    changes = []
    if names is None:
        names = ['framework/services.jar', 'framework/services.jar.prof'] + [
            'framework/oat/arm/services.' + x for x in ('art', 'odex', 'vdex')]
    for name in names:
        src = (tree / name).resolve()
        if not src.is_file():
            raise ValueError('missing compiled artifact: ' + name)
        # Debugfs commands do not use shell quoting. Reject ambiguous names.
        if any(c.isspace() or c in '\"\\' for c in str(src) + str(backup)):
            raise ValueError('unsafe debugfs host path')
        target = '/' + name
        metadata = run(image, 'stat ' + target).decode()
        mode = re.search(r'Type: regular\s+Mode:\s+(0?[0-7]{3,4})', metadata)
        owner = re.search(r'User:\s+(\d+)\s+Group:\s+(\d+)', metadata)
        if not mode or not owner:
            raise ValueError('expected existing regular artifact: ' + name)
        if 'security.selinux (26)' not in metadata or 'u:object_r:system_file:s0' not in metadata:
            raise ValueError('unexpected SELinux attributes: ' + name)
        # Refuse to silently drop unrelated attributes.
        attrs = run(image, 'ea_list ' + target).decode()
        if len(re.findall(r'^\s+\S+ \(\d+\) =', attrs, re.M)) != 1:
            raise ValueError('unexpected extended attributes: ' + name)
        old = (backup / name).resolve()
        old.parent.mkdir(parents=True, exist_ok=True)
        run(image, 'dump ' + target + ' ' + str(old))
        if not old.is_file():
            raise ValueError('backup failed: ' + name)
        before = digest(old)
        run(image, 'rm ' + target, True)
        run(image, 'write ' + str(src) + ' ' + target, True)
        for field, value in [('mode', '0' + format(stat.S_IFREG | int(mode.group(1), 8), 'o')),
                             ('uid', owner.group(1)), ('gid', owner.group(2))]:
            run(image, 'set_inode_field ' + target + ' ' + field + ' ' + value, True)
        with tempfile.TemporaryDirectory(prefix='z1-framework-') as temp:
            label = Path(temp) / 'label'
            label.write_bytes(b'u:object_r:system_file:s0\0')
            run(image, 'ea_set -f ' + str(label) + ' ' + target + ' security.selinux', True)
            copied = Path(temp) / 'payload'
            run(image, 'dump ' + target + ' ' + str(copied))
            if digest(copied) != digest(src):
                raise ValueError('replacement digest mismatch: ' + name)
        result = run(image, 'stat ' + target).decode()
        if not re.search(r'Mode:\s+' + mode.group(1) + r'\b', result):
            raise ValueError('mode preservation failed: ' + name)
        if not re.search(r'User:\s+' + owner.group(1) + r'\s+Group:\s+' + owner.group(2), result):
            raise ValueError('owner preservation failed: ' + name)
        if 'security.selinux (26)' not in result or 'u:object_r:system_file:s0' not in result:
            raise ValueError('label preservation failed: ' + name)
        changes.append(dict(path=name, before_sha256=before, after_sha256=digest(src),
                            size=src.stat().st_size, metadata_preserved=True))
    return dict(result='OFFLINE_FRAMEWORK_REPLACEMENT_PASS', changes=changes,
                device_runtime_verified=False)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--system', type=Path, required=True)
    ap.add_argument('--tree', type=Path, required=True)
    ap.add_argument('--backup', type=Path, required=True)
    ap.add_argument('--report', type=Path, required=True)
    a = ap.parse_args()
    report = replace(a.system, a.tree, a.backup)
    a.report.write_text(json.dumps(report, indent=2) + '\n')
    print(report['result'])


if __name__ == '__main__':
    main()

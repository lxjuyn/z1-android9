#!/usr/bin/env python3
"""Check that every NEEDED library of the deployed HALs exists in the image.

For each .so / HIDL service in the interesting directories, read its DT_NEEDED
entries (readelf on a dumped copy) and resolve them against the image's library
search roots (/vendor/lib, /lib, and their /hw subdirs). Reports unresolved
dependencies, which are exactly the "the HAL silently fails to load" cases.
"""
import os
import re
import subprocess
import sys
import tempfile

IMG = sys.argv[1] if len(sys.argv) > 1 else '/home/lxj/claude/6572/out/z1_r3f_system/system.img'
ROOTS = ['/vendor/lib', '/vendor/lib/hw', '/lib', '/lib/hw', '/vendor/lib/egl']


def ls(path):
    out = subprocess.run(['debugfs', '-R', f'ls {path}', IMG],
                         capture_output=True, text=True).stdout
    return {t for t in out.replace('\n', ' ').split(' ')
            if t and t not in ('.', '..') and not t.isdigit() and not t.startswith('(')}


def have(name):
    for root in ROOTS:
        if name in ls(root):
            return True
    # framework libs may live deeper
    return False


def needed(path):
    with tempfile.NamedTemporaryFile() as tf:
        subprocess.run(['debugfs', '-R', f'dump {path} {tf.name}', IMG],
                       capture_output=True)
        out = subprocess.run(['readelf', '-d', tf.name], capture_output=True, text=True).stdout
    return re.findall(r'Shared library: \[(.*?)\]', out)


def main():
    targets = []
    for root in ('/vendor/lib/hw', '/vendor/bin/hw', '/vendor/lib', '/lib/hw'):
        for name in sorted(ls(root)):
            if name.endswith('.so') or 'service' in name:
                targets.append(f'{root}/{name}')

    bad = 0
    for path in targets:
        miss = [n for n in needed(path) if not have(n)]
        if miss:
            bad += 1
            print(f'UNRESOLVED {path}')
            for m in miss:
                print(f'    needs {m}')
    print(f'\nchecked {len(targets)} binaries, {bad} with unresolved dependencies')


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Apply checked Z1 patches to prepared Mesa 19.1.8; refuse unknown edits.

Only six Lima files are expanded to a temporary directory. Repeated runs
compare exact expected bytes and do not rewrite already patched files.
"""
import argparse
import hashlib
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile

FILES = ('lima_context.h', 'lima_draw.c', 'lima_submit.c', 'lima_submit.h', 'lima_fence.c', 'lima_screen.c')
BASE = Path('src/gallium/drivers/lima')
PATCHES = ('0001-lima-19.1.8-pp-stream-cache-miss.patch',
           '0002-lima-19.1.8-empty-flush-fence.patch')


def compat(text):
    for name in ('drmSyncobjCreate', 'drmSyncobjDestroy', 'drmSyncobjImportSyncFile',
                 'drmSyncobjWait', 'drmSyncobjExportSyncFile'):
        text = re.sub(r'\b'+name+r'\b', 'z1_'+name, text)
    text = re.sub(r'\bDRM_SYNCOBJ_CREATE_SIGNALED\b', 'Z1_DRM_SYNCOBJ_CREATE_SIGNALED', text)
    return text.replace('#include "drm-uapi/lima_drm.h"',
                        '#include "drm-uapi/lima_drm.h"\n#include "lima_syncobj_compat.h"')


def expected_files(archive):
    digest = hashlib.sha256()
    with Path(archive).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            digest.update(chunk)
    if digest.hexdigest() != 'f0fe8289b7d147943bf2fc2147833254881577e8f9ed3d94ddb39e430e711725':
        raise ValueError('not the verified Mesa 19.1.8 archive')
    patch_dir = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(prefix='z1-mesa-patches-') as tmp:
        root = Path(tmp)
        before = {}
        with tarfile.open(archive, 'r:xz') as tar:
            for name in FILES:
                path = BASE/name
                data = tar.extractfile('mesa-19.1.8/'+path.as_posix()).read().decode()
                before[path] = compat(data) if name == 'lima_submit.c' else data
                dest = root/path
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(data)
        for patch in PATCHES:
            subprocess.run(['patch', '--batch', '--forward', '--fuzz=0', '-p1',
                            '-i', str(patch_dir/patch)], cwd=root, check=True,
                           capture_output=True, text=True, timeout=30)
        after = {path: (compat((root/path).read_text()) if path.name == 'lima_submit.c'
                        else (root/path).read_text()) for path in before}
    return before, after


def apply(source, archive):
    before, after = expected_files(archive)
    # Preflight every file before changing any, including partially applied runs.
    current = {path: (source/path).read_text() for path in before}
    for path, data in current.items():
        if data not in (before[path], after[path]):
            raise ValueError('unknown Lima edits; refusing to overwrite: '+str(source/path))
    changed = []
    for path, data in current.items():
        if data != after[path]:
            (source/path).write_text(after[path])
            changed.append(path.as_posix())
    return changed


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--archive', type=Path, required=True)
    args = parser.parse_args()
    print('Z1 Lima patches verified; changed:', ', '.join(apply(args.source, args.archive)) or 'none')

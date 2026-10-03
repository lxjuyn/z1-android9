#!/usr/bin/env python3
"""Replace only init in a new stock S1 diagnostic boot image; never flash."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import struct

from z1_add_bootdiag import entries, encode
from z1_make_stock_android9 import parse_boot, elf_static_arm, LIMIT


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--base', required=True, type=Path)
    ap.add_argument('--init', required=True, type=Path)
    ap.add_argument('--output', required=True, type=Path)
    args = ap.parse_args()
    if args.output.exists():
        raise ValueError('output exists; preserve prior candidate')
    base = args.base.read_bytes()
    page, kernel, oldrd, _ = parse_boot(base)
    newinit = args.init.read_bytes()
    elf_static_arm(newinit)
    if b'Z1STOCK-DIAG' not in newinit:
        raise ValueError('init lacks diagnostic breadcrumbs')
    before = entries(gzip.decompress(oldrd[512:]))
    after = []
    count = 0
    for name, fields, data in before:
        if name.lstrip(b'./') == b'init':
            data = newinit
            count += 1
        after.append((name, fields, data))
    if count != 1:
        raise ValueError('expected one init executable')
    archive = b''.join(encode(*item) for item in after)
    archive += b'\0' * (-len(archive) % 512)
    actual = entries(archive)
    changed = [n.decode() for (n, _, old), (_, _, new) in zip(before, actual) if old != new]
    if changed not in (['init'], ['./init']):
        raise ValueError('unexpected ramdisk changes: ' + repr(changed))
    body = gzip.compress(archive, compresslevel=9, mtime=0)
    mtk = bytearray(oldrd[:512])
    struct.pack_into('<I', mtk, 4, len(body))
    rd = bytes(mtk) + body
    header = bytearray(base[:page])
    struct.pack_into('<I', header, 16, len(rd))
    ident = hashlib.sha1()
    for payload in (kernel, rd, b''):
        ident.update(payload)
        ident.update(struct.pack('<I', len(payload)))
    header[576:608] = ident.digest() + b'\0' * 12
    def pad(data):
        return data + b'\0' * (-len(data) % page)
    image = bytes(header) + pad(kernel) + pad(rd)
    if len(image) > LIMIT:
        raise ValueError('boot exceeds partition')
    _, checkkernel, checkrd, _ = parse_boot(image)
    assert checkkernel == kernel and checkrd == rd
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(image)
    report = dict(status='STATIC_DIAGNOSTIC_BOOT_PASS', device_runtime_verified=False,
                  changed_ramdisk_files=changed, base_sha256=sha(base),
                  boot_sha256=sha(image), init_sha256=sha(newinit), boot_bytes=len(image),
                  system='Use the unchanged paired stock S1 system.img',
                  purpose='Capture early init fatal cause; no policy errors suppressed')
    args.output.with_suffix('.validation.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()

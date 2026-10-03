#!/usr/bin/env python3
"""Add a static, read-only diagnostic to a NEW Android ramdisk (no flashing)."""
import argparse
import gzip
import struct
from pathlib import Path
from z1_prepare_android9_ramdisk import check_newc


def entries(archive):
    check_newc(archive)
    offset = 0
    result = []
    while True:
        header = archive[offset:offset + 110]
        fields = [int(header[p:p + 8], 16) for p in range(6, 110, 8)]
        name_end = offset + 110 + fields[11]
        name = archive[offset + 110:name_end - 1]
        start = (name_end + 3) & ~3
        end = start + fields[6]
        result.append((name, fields, archive[start:end]))
        offset = (end + 3) & ~3
        if name == b'TRAILER!!!':
            return result


def encode(name, fields, data):
    fields = fields.copy()
    fields[6], fields[11], fields[12] = len(data), len(name) + 1, 0
    record = b'070701' + b''.join(f'{v:08x}'.encode() for v in fields)
    record += name + b'\0'
    record += b'\0' * (-len(record) % 4)
    record += data
    return record + b'\0' * (-len(record) % 4)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('ramdisk', type=Path, help='standard gzip-cpio ramdisk')
    parser.add_argument('helper', type=Path, help='static ARM32 ELF diagnostic')
    parser.add_argument('output', type=Path, help='new standard ramdisk path')
    parser.add_argument('--usb-acm', action='store_true',
                        help='start ramdisk ACM logger and remove the system-dependent ADB helper')
    parser.add_argument('--logcat-relay', action='store_true',
                        help='forward complete Android main/system/crash lines directly over ACM')
    args = parser.parse_args()
    if args.logcat_relay and not args.usb_acm:
        parser.error('--logcat-relay requires --usb-acm (ACM owns the diagnostic FIFO)')
    elf = args.helper.read_bytes()
    if elf[:6] != b'\x7fELF\x01\x01' or struct.unpack_from('<H', elf, 18)[0] != 40:
        raise ValueError('helper must be little-endian ARM32 ELF')
    phoff = struct.unpack_from('<I', elf, 28)[0]
    phsize, phnum = struct.unpack_from('<HH', elf, 42)
    if any(struct.unpack_from('<I', elf, phoff + i * phsize)[0] == 3
           for i in range(phnum)):
        raise ValueError('helper has PT_INTERP; it must work without /system')
    original = entries(gzip.decompress(args.ramdisk.read_bytes()))
    if any(n.lstrip(b'./') == b'z1_bootdiag' for n, _, _ in original):
        raise ValueError('diagnostic already present')
    rc_matches = [n for n, _, _ in original if n in (b'init.z1.rc', b'./init.z1.rc')]
    if len(rc_matches) != 1:
        raise ValueError('expected exactly one init.z1.rc')
    rc_name = rc_matches[0]
    extra = (b'\n# Diagnostic only: runs without /system, before post-fs-data swap.\n'
             b'on post-fs\n    exec_background u:r:init:s0 root root -- /z1_bootdiag\n')
    if args.usb_acm:
        extra += (b'\n# Independent diagnostic transport; helper handles configfs and late USB attach.\n'
                  b'on init\n    start z1_acm_log\n\n'
                  b'service z1_acm_log /z1_bootdiag --acm\n'
                  b'    class core\n    user root\n    group root\n'
                  b'    disabled\n    seclabel u:r:init:s0\n')
    if args.logcat_relay:
        extra += (b'\n# Forward Android logs directly over diagnostic ACM, bypassing printk rate limits.\n'
                  b'on post-fs-data\n    start z1_logcat_relay\n\n'
                  b'service z1_logcat_relay /z1_bootdiag --logcat\n'
                  b'    class core\n    user root\n    group log\n'
                  b'    disabled\n    seclabel u:r:init:s0\n')
    updated = []
    for name, fields, data in original:
        if name == rc_name:
            if args.usb_acm:
                action = b'on boot\n    start z1_usb_setup\n'
                service_start = data.find(b'service z1_usb_setup ')
                service_end = data.find(b'service watchdogd ', service_start)
                if data.count(action) != 1 or service_start < 0 or service_end < 0:
                    raise ValueError('cannot safely identify original ADB helper action/service')
                data = (data[:service_start] + data[service_end:]).replace(action, b'')
            data += extra
        if name == b'TRAILER!!!':
            helper_fields = [max(f[0] for _, f, _ in original) + 1,
                             0o100755, 0, 0, 1, 0, len(elf), 0, 0, 0, 0, 0, 0]
            updated.append((b'z1_bootdiag', helper_fields, elf))
        updated.append((name, fields, data))
    archive = b''.join(encode(*entry) for entry in updated)
    archive += b'\0' * (-len(archive) % 512)
    verified = entries(archive)
    before = {n: d for n, _, d in original}
    after = {n: d for n, _, d in verified}
    assert all(after[n] == d for n, d in before.items() if n != rc_name)
    assert after[rc_name].endswith(extra)
    if not args.usb_acm:
        assert after[rc_name] == before[rc_name] + extra
    else:
        assert b'start z1_usb_setup' not in after[rc_name]
        assert b'service watchdogd /sbin/watchdogd 5 10' in after[rc_name]
        assert b'mount_all /fstab.z1' in after[rc_name]
    if args.logcat_relay:
        assert b'service z1_logcat_relay /z1_bootdiag --logcat' in after[rc_name]
        assert b'on post-fs-data\n    swapon_all /fstab.z1' in after[rc_name]
    assert after[b'z1_bootdiag'] == elf
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('xb') as out:
        out.write(gzip.compress(archive, compresslevel=9, mtime=0))
    print(f'{args.output}: {args.output.stat().st_size} bytes; other file contents preserved')


if __name__ == '__main__':
    main()

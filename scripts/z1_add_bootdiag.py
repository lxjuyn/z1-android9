#!/usr/bin/env python3
"""Add a static, read-only diagnostic to a NEW Android ramdisk (no flashing)."""
import argparse
import gzip
import re
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


def cpio_path(name):
    while name.startswith(b'./'):
        name = name[2:]
    return name


def strip_z1_adb_gadget(rc):
    """Remove device init sections that configured the competing ADB gadget."""
    lines = rc.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines)
              if re.match(rb'^(?:on|service)\s+', line)]
    output = []
    cursor = 0
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(lines)
        output.extend(lines[cursor:start])
        section = b''.join(lines[start:end])
        header = lines[start].strip()
        is_setup_service = header.startswith(b'service z1_usb_setup ')
        is_adb_action = header.startswith(b'on ') and any(
            marker in section for marker in (
                b'start z1_usb_setup', b'/config/usb_gadget/g1',
                b'mount functionfs adb', b'/dev/usb-ffs/adb',
            )
        )
        if not is_setup_service and not is_adb_action:
            output.extend(lines[start:end])
        cursor = end
    output.extend(lines[cursor:])
    result = b''.join(output)
    active = b'\n'.join(line.split(b'#', 1)[0]
                        for line in result.splitlines())
    forbidden = (b'z1_usb_setup', b'/config/usb_gadget/g1',
                 b'mount functionfs adb', b'/dev/usb-ffs/adb', b'ffs.adb')
    if any(token in active for token in forbidden):
        raise ValueError('ADB gadget setup remains in init.z1.rc')
    stale_config_reset = re.compile(
        rb'^on property:ro\.persistent_properties\.ready=true\s*\n'
        rb'\s+setprop persist\.sys\.usb\.config none\s*\n'
        rb'\s+setprop sys\.usb\.config none\s*$', re.M
    )
    if not stale_config_reset.search(active):
        raise ValueError('init.z1.rc does not override persisted USB ADB settings')
    return result


def strip_generic_usb_imports(rc):
    """Remove both generic Android USB rc imports from root init.rc."""
    targets = {b'/init.usb.rc', b'/init.usb.configfs.rc'}
    removed = {target: 0 for target in targets}
    output = []
    for line in rc.splitlines(keepends=True):
        code = line.split(b'#', 1)[0].strip()
        fields = code.split()
        if len(fields) == 2 and fields[0] == b'import' and fields[1] in targets:
            removed[fields[1]] += 1
            continue
        output.append(line)
    if any(count != 1 for count in removed.values()):
        raise ValueError('expected one import each for init.usb.rc and init.usb.configfs.rc')
    return b''.join(output)


def verify_acm_is_single_init_owner(archive):
    """Reject any remaining init rule that can bind an Android ADB gadget."""
    forbidden = (
        b'/config/usb_gadget/', b'/sys/class/android_usb/',
        b'mount functionfs adb', b'/dev/usb-ffs/adb', b'ffs.adb',
        b'service adbd ', b'start adbd', b'start z1_usb_setup',
    )
    for name, _, data in archive:
        if not cpio_path(name).endswith(b'.rc'):
            continue
        active = b'\n'.join(line.split(b'#', 1)[0]
                            for line in data.splitlines())
        if any(token in active for token in forbidden):
            raise ValueError(f'competing USB gadget rule remains in {name!r}')
        if re.search(rb'^\s*import\s+\S*init\.usb(?:\.configfs)?\.rc\b',
                     active, re.M):
            raise ValueError(f'generic Android USB rc import remains in {name!r}')
        if re.search(rb'^\s*on\s+property:sys\.usb\.(?:config|ffs\.ready)=',
                     active, re.M):
            raise ValueError(f'generic sys.usb gadget trigger remains in {name!r}')
        for line in active.splitlines():
            fields = line.split()
            if len(fields) >= 3 and fields[0] == b'setprop':
                if fields[1] in (b'sys.usb.config', b'persist.sys.usb.config') and fields[2] != b'none':
                    raise ValueError(f'non-none generic USB config remains in {name!r}')
                if fields[1] == b'sys.usb.configfs':
                    raise ValueError(f'generic configfs selection remains in {name!r}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('ramdisk', type=Path, help='standard gzip-cpio ramdisk')
    parser.add_argument('helper', type=Path, help='static ARM32 ELF diagnostic')
    parser.add_argument('output', type=Path, help='new standard ramdisk path')
    parser.add_argument('--usb-acm', action='store_true',
                        help='make ramdisk ACM the only USB gadget owner and remove ADB setup')
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
    root_rc_names = [n for n, _, _ in original
                     if cpio_path(n) == b'init.rc']
    if args.usb_acm and len(root_rc_names) != 1:
        raise ValueError('USB ACM mode requires exactly one root init.rc')
    root_rc_name = root_rc_names[0] if root_rc_names else None
    usb_rcs = {b'init.usb.rc', b'init.usb.configfs.rc'}
    usb_rc_names = [n for n, _, _ in original if cpio_path(n) in usb_rcs]
    usb_counts = {path: sum(cpio_path(n) == path for n in usb_rc_names)
                  for path in usb_rcs}
    if args.usb_acm and any(count != 1 for count in usb_counts.values()):
        raise ValueError('USB ACM mode requires both generic USB rc files to remove')
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
        canonical = cpio_path(name)
        if args.usb_acm and canonical in usb_rcs:
            continue
        if name == rc_name:
            if args.usb_acm:
                data = strip_z1_adb_gadget(data)
            data += extra
        if args.usb_acm and name == root_rc_name:
            data = strip_generic_usb_imports(data)
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
    removed_names = set(usb_rc_names) if args.usb_acm else set()
    modified_names = {rc_name}
    if args.usb_acm:
        modified_names.add(root_rc_name)
    assert all(after[n] == d for n, d in before.items()
               if n not in modified_names and n not in removed_names)
    assert after[rc_name].endswith(extra)
    if not args.usb_acm:
        assert after[rc_name] == before[rc_name] + extra
    else:
        assert not any(cpio_path(n) in usb_rcs for n, _, _ in verified)
        assert b'/init.usb.rc' not in after[root_rc_name]
        assert b'/init.usb.configfs.rc' not in after[root_rc_name]
        assert b'setprop persist.sys.usb.config none' in after[rc_name]
        assert b'setprop sys.usb.config none' in after[rc_name]
        verify_acm_is_single_init_owner(verified)
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

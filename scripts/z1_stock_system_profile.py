#!/usr/bin/env python3
"""Disable unsupported remote HIDL servers in a NEW stock diagnostic ext4.

Preserve inode metadata and SELinux xattrs. Only edit an existing single-block
RC payload and its size; never operate on block devices. Keep native Binder
services intact. The staging files are updated to match for closure auditing.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import stat
import subprocess

from z1_verify_init_closure import services


def command(image, text, write=False):
    result = subprocess.run(['debugfs']+(['-w'] if write else [])+
                            ['-R', text, str(image)], check=True,
                            capture_output=True, timeout=30)
    return result.stdout


def unsupported(service):
    return (service['name'] in ('hwservicemanager', 'vndservicemanager', 'hidl_memory', 'media.codec')
            or service['executable'].startswith('/vendor/bin/hw/')
            or service['executable'].startswith('/system/bin/hw/'))


def ext4_block_size(system):
    system = Path(system)
    if not stat.S_ISREG(system.stat().st_mode):
        raise ValueError('only regular new image files may be edited')
    superblock = subprocess.run(['dumpe2fs', '-h', str(system)], check=True,
                               capture_output=True, text=True, timeout=30).stdout
    bs = re.search(r'^Block size:\s+(\d+)$', superblock, re.M)
    if not bs or int(bs.group(1)) != 4096 or 'metadata_csum' in superblock:
        raise ValueError('unsupported ext4 profile for preserving RC inodes')
    return int(bs.group(1))


def edit_existing_rc(system, tree, backup, rc, original, modified, block_size):
    """Rewrite one allocated block without replacing its inode or xattrs."""
    relative = rc.relative_to(tree).as_posix()
    if len(modified) > block_size:
        raise ValueError('RC modification exceeds existing single block')
    if command(system, 'cat /'+relative) != original:
        raise ValueError('image/staging RC mismatch: '+relative)
    blocks = command(system, 'blocks /'+relative).decode().split()
    if len(blocks) != 1 or not blocks[0].isdigit():
        raise ValueError('RC must occupy exactly one existing ext4 data block: '+relative)
    dest = backup/relative
    if dest.exists():
        raise ValueError('RC backup already exists: '+str(dest))
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(rc, dest)
    with system.open('r+b') as image:
        image.seek(int(blocks[0])*block_size)
        image.write(modified+b'\0'*(block_size-len(modified)))
    command(system, 'set_inode_field /'+relative+' size '+str(len(modified)), write=True)
    if command(system, 'cat /'+relative) != modified:
        raise ValueError('image RC edit verification failed: '+relative)
    rc.write_bytes(modified)
    return dict(path=relative, before_sha256=hashlib.sha256(original).hexdigest(),
                after_sha256=hashlib.sha256(modified).hexdigest())


def suppress_stock_audio_restarts(text):
    """Comment only the two remote HAL restarts in audioserver's stanza."""
    active = False
    changed = []
    result = []
    pattern = re.compile(r'^\s+onrestart\s+restart\s+'
                         r'(vendor\.audio-hal-2-0|audio-hal-2-0)(?:\s+#.*)?\s*$')
    for line in text.splitlines(keepends=True):
        if line.strip() and not line[0].isspace() and not line.startswith('#'):
            tokens = line.split()
            active = len(tokens) >= 2 and tokens[:2] == ['service', 'audioserver']
        match = pattern.fullmatch(line) if active else None
        if match:
            indent = line[:len(line)-len(line.lstrip())]
            line = indent+'# stock: remote audio HAL unavailable; '+line.lstrip()
            changed.append(match.group(1))
        result.append(line)
    return ''.join(result), changed


def apply_stock_audio_restart_profile(system, tree, backup):
    """Standalone, idempotent pass for a derived image already HIDL-profiled.

    Return change records. Does not add/remove disabled options or touch native
    audioserver service settings. Backup may be a new or existing empty root.
    """
    system, tree, backup = map(Path, (system, tree, backup))
    block_size = ext4_block_size(system)
    changes = []
    found = False
    for rc in sorted((tree/'etc/init').glob('*.rc')):
        original = rc.read_bytes()
        parsed = services(original.decode(), rc.relative_to(tree).as_posix())
        if not any(service['name'] == 'audioserver' for service in parsed):
            continue
        found = True
        modified, restarts = suppress_stock_audio_restarts(original.decode())
        if not restarts:
            # Even a no-op pass must validate it is looking at the same image.
            if command(system, 'cat /'+rc.relative_to(tree).as_posix()) != original:
                raise ValueError('image/staging RC mismatch: '+str(rc))
            continue
        change = edit_existing_rc(system, tree, backup, rc, original,
                                  modified.encode(), block_size)
        change['suppressed_restarts'] = restarts
        changes.append(change)
    if not found:
        raise ValueError('audioserver service RC was not found')
    return changes


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--system', type=Path, required=True)
    ap.add_argument('--tree', type=Path, required=True)
    ap.add_argument('--backup', type=Path, required=True)
    ap.add_argument('--report', type=Path, required=True)
    ap.add_argument('--audio-restart-only', action='store_true',
                    help='only suppress remote audio onrestart hooks in an already-profiled image')
    args = ap.parse_args()
    if not stat.S_ISREG(args.system.stat().st_mode):
        raise ValueError('only regular new image files may be edited')
    if args.backup.exists():
        raise ValueError('backup directory already exists')
    args.backup.mkdir(parents=True)
    block_size = ext4_block_size(args.system)
    if args.audio_restart_only:
        changes = apply_stock_audio_restart_profile(args.system, args.tree, args.backup)
        report = dict(result='STATIC_STOCK_AUDIO_RESTART_PROFILE_PASS',
                      device_runtime_verified=False,
                      method='preserve allocated RC data blocks/inode ownership/mode/xattrs',
                      changes=changes)
        args.report.write_text(json.dumps(report, indent=2)+'\n')
        print('Suppressed stock remote audio restart hooks in', len(changes), 'RC files')
        return
    changes = []
    hardware_manager_found = False
    candidates = sorted((args.tree/'etc/init').glob('*.rc'))
    candidates += sorted((args.tree/'vendor/etc/init').glob('*.rc'))
    for rc in candidates:
        relative = rc.relative_to(args.tree).as_posix()
        original = rc.read_bytes()
        parsed = services(original.decode(), relative)
        hardware_manager_found |= any(s['name'] == 'hwservicemanager' for s in parsed)
        names = {s['name'] for s in parsed if unsupported(s) and ['disabled'] not in s['options']}
        result = []
        for line in original.decode().splitlines(keepends=True):
            result.append(line)
            if line.startswith('service ') and line.split()[1] in names:
                result.append('    disabled\n')
        modified_text, restarts = suppress_stock_audio_restarts(''.join(result))
        modified = modified_text.encode()
        if modified == original:
            continue
        change = edit_existing_rc(args.system, args.tree, args.backup, rc,
                                  original, modified, block_size)
        change['disabled_services'] = sorted(names)
        if restarts:
            change['suppressed_restarts'] = restarts
        changes.append(change)
    if not hardware_manager_found:
        raise ValueError('hardware manager RC was not found')
    changes += apply_stock_audio_restart_profile(args.system, args.tree, args.backup)
    report = dict(result='STATIC_STOCK_SERVICE_PROFILE_PASS', device_runtime_verified=False,
                  method='preserve allocated RC data blocks/inode ownership/mode/xattrs', changes=changes)
    args.report.write_text(json.dumps(report, indent=2)+'\n')
    print('Disabled unsupported HIDL servers in', len(changes), 'RC files; native Binder services preserved')


if __name__ == '__main__':
    main()

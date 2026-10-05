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
import shlex
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
            # Pie healthd publishes a remote health HAL; the stock product uses
            # BatteryService's sysfs monitor instead. Match this service exactly.
            or (service['name'] == 'healthd' and service['executable'] == '/system/bin/healthd')
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


def remove_servicemanager_healthd_hook(text):
    """Remove a prevalidated physical hook, preserving every other line."""
    active = False
    retained = []
    for line in text.splitlines(keepends=True):
        if line.strip() and not line[0].isspace() and not line.lstrip().startswith('#'):
            active = shlex.split(line, comments=True) == [
                'service', 'servicemanager', '/system/bin/servicemanager']
        if active and shlex.split(line, comments=True) == ['onrestart', 'restart', 'healthd']:
            continue
        retained.append(line)
    return ''.join(retained)


def stock_healthd_restart_plan(tree):
    """Find one exact servicemanager stanza and at most its single healthd hook.

    Plan before edits; reject duplicate/continued hooks instead of guessing.
    A hook already removed is an idempotent no-op, still checked against image.
    """
    matches = []
    candidates = sorted((tree/'etc/init').glob('*.rc'))
    candidates += sorted((tree/'vendor/etc/init').glob('*.rc'))
    for rc in candidates:
        original = rc.read_bytes()
        text = original.decode()
        for service in services(text, rc.relative_to(tree).as_posix()):
            if service['name'] != 'servicemanager':
                continue
            lines = text.splitlines(keepends=True)
            if shlex.split(lines[service['line']-1], comments=True) != [
                    'service', 'servicemanager', '/system/bin/servicemanager']:
                raise ValueError('unexpected servicemanager stanza: '+str(rc))
            hooks = [option for option in service['options']
                     if option[:3] == ['onrestart', 'restart', 'healthd']]
            if len(hooks) > 1 or any(hook != ['onrestart', 'restart', 'healthd'] for hook in hooks):
                raise ValueError('ambiguous servicemanager healthd restart hook: '+str(rc))
            indexes = []
            for i in range(service['line'], len(lines)):
                line = lines[i]
                if line.strip() and not line[0].isspace() and not line.lstrip().startswith('#'):
                    break
                if shlex.split(line, comments=True) == ['onrestart', 'restart', 'healthd']:
                    indexes.append(i)
            if len(indexes) != len(hooks):
                raise ValueError('continued/ambiguous healthd hook: '+str(rc))
            modified = ''.join(line for i, line in enumerate(lines) if i not in indexes).encode()
            matches.append((rc, original, modified, bool(hooks)))
    if len(matches) != 1:
        raise ValueError('expected one exact servicemanager stanza; found '+str(len(matches)))
    return matches[0]


def apply_healthd_restart_plan(system, tree, backup, plan, block_size):
    rc, original, modified, removed = plan
    if original != modified:
        change = edit_existing_rc(system, tree, backup, rc, original, modified, block_size)
    else:
        relative = rc.relative_to(tree).as_posix()
        if command(system, 'cat /'+relative) != original:
            raise ValueError('image/staging RC mismatch: '+relative)
        digest = hashlib.sha256(original).hexdigest()
        change = dict(path=relative, before_sha256=digest, after_sha256=digest)
    change.update(status='healthd_restart_removed' if removed else 'healthd_restart_absent',
                  removed_restart_hooks=['healthd'] if removed else [])
    return change


def apply_stock_healthd_only_profile(system, tree, backup):
    """Disable exact healthd and remove its servicemanager restart path."""
    system, tree, backup = map(Path, (system, tree, backup))
    block_size = ext4_block_size(system)
    candidates = sorted((tree/'etc/init').glob('*.rc'))
    candidates += sorted((tree/'vendor/etc/init').glob('*.rc'))
    matches = []
    for rc in candidates:
        relative = rc.relative_to(tree).as_posix()
        original = rc.read_bytes()
        parsed = services(original.decode(), relative)
        lines = original.decode().splitlines(keepends=True)
        for service in parsed:
            if (service['name'] != 'healthd' or
                    service['executable'] != '/system/bin/healthd'):
                continue
            header = lines[service['line']-1]
            try:
                header_tokens = shlex.split(header, comments=True)
            except ValueError as exc:
                raise ValueError(f'malformed healthd service header: {relative}') from exc
            # Do not alter a service that merely shares the name/executable but
            # adds command-line arguments; only the requested exact stanza fits.
            if header_tokens == ['service', 'healthd', '/system/bin/healthd']:
                matches.append((rc, relative, original, service, lines))

    if len(matches) != 1:
        raise ValueError('expected exactly one exact healthd service; found '+str(len(matches)))

    restart_plan = stock_healthd_restart_plan(tree)
    rc, relative, original, service, lines = matches[0]
    if ['critical'] not in service['options']:
        raise ValueError('exact healthd service is missing its original critical option')
    if command(system, 'cat /'+relative) != original:
        raise ValueError('image/staging RC mismatch: '+relative)

    already_disabled = ['disabled'] in service['options']
    if not already_disabled:
        header_index = service['line']-1
        header = lines[header_index]
        line_ending = '\r\n' if header.endswith('\r\n') else '\n'
        if not header.endswith(('\n', '\r')):
            lines[header_index] = header+line_ending
        lines.insert(header_index+1, '    disabled'+line_ending)
    modified = ''.join(lines).encode()
    same_rc = restart_plan[0] == rc
    if same_rc:
        # Combine edits into one block update and one original backup.
        modified = remove_servicemanager_healthd_hook(modified.decode()).encode()
    if modified != original:
        change = edit_existing_rc(system, tree, backup, rc, original, modified, block_size)
    else:
        digest = hashlib.sha256(original).hexdigest()
        change = dict(path=relative, before_sha256=digest, after_sha256=digest)
    change.update(status='already_disabled' if already_disabled else 'disabled_added',
                  disabled_service='healthd', critical_preserved=True)
    if same_rc:
        change['removed_restart_hooks'] = ['healthd'] if restart_plan[3] else []
        return [change]
    return [change, apply_healthd_restart_plan(system, tree, backup, restart_plan, block_size)]


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
    ap.add_argument('--healthd-only', action='store_true',
                    help='disable exact stock healthd and remove its servicemanager restart hook')
    args = ap.parse_args()
    if args.audio_restart_only and args.healthd_only:
        ap.error('--audio-restart-only and --healthd-only are separate modes')
    if not stat.S_ISREG(args.system.stat().st_mode):
        raise ValueError('only regular new image files may be edited')
    if args.backup.exists():
        raise ValueError('backup directory already exists')
    args.backup.mkdir(parents=True)
    if args.healthd_only:
        changes = apply_stock_healthd_only_profile(args.system, args.tree, args.backup)
        report = dict(result='STATIC_STOCK_HEALTHD_ONLY_PROFILE_PASS',
                      device_runtime_verified=False,
                      method='preserve allocated RC data blocks/inode ownership/mode/xattrs',
                      changes=changes)
        args.report.write_text(json.dumps(report, indent=2)+'\n')
        print('Disabled exact stock healthd and removed its servicemanager restart path')
        return
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
    restart_plan = stock_healthd_restart_plan(args.tree)
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
        base_text = ''.join(result)
        if rc == restart_plan[0]:
            if original != restart_plan[1]:
                raise ValueError('servicemanager RC changed after preflight')
            base_text = remove_servicemanager_healthd_hook(base_text)
        modified_text, restarts = suppress_stock_audio_restarts(base_text)
        modified = modified_text.encode()
        if modified == original:
            if rc == restart_plan[0] and command(args.system, 'cat /'+relative) != original:
                raise ValueError('image/staging RC mismatch: '+relative)
            continue
        change = edit_existing_rc(args.system, args.tree, args.backup, rc,
                                  original, modified, block_size)
        change['disabled_services'] = sorted(names)
        if rc == restart_plan[0]:
            change['removed_restart_hooks'] = ['healthd'] if restart_plan[3] else []
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

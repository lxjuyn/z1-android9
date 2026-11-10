#!/usr/bin/env python3
"""Read-only ACM+ADB candidate predicate checker; never probes this host or binds.

Inputs: an extracted rootfs (system/ included) and an explicit JSON observation.
Use --phase start before adbd: ep0 and FFS ready are deliberately NOT required.
Use --phase bind only after adbd wrote descriptors. Reports are NOT_DEPLOYED even
for successful mocks; neither a JSON snapshot nor ELF magic proves secure adbd
behavior, policy compatibility, USB enumeration or actual host authorization.

Observation schema is demonstrated by tests/test_z1_usb_candidate.py: properties,
udcs [{name, driver}], bound_gadgets {gadget: controller}, configfs_mounted,
selinux_enforcing; bind also needs ffs_mount, ep0_type and ffs_kernel_ready.
No missing/unknown controller, auth value or readiness is silently defaulted.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import stat
import sys

MUSB = re.compile(r"musb-hdrc(?:\.[0-9]+\.auto)?\Z")
GADGET = "/config/usb_gadget/z1_candidate"


class InvalidInput(ValueError):
    pass


def rooted(root, absolute):
    """Reject traversal and host symlink escape; do not follow Android host paths."""
    if not isinstance(absolute, str) or not absolute.startswith('/'):
        raise InvalidInput('expected absolute image path')
    if '..' in Path(absolute).parts or '\0' in absolute:
        raise InvalidInput('unsafe image path: ' + absolute)
    path = root / absolute.lstrip('/')
    if not path.resolve().is_relative_to(root):
        raise InvalidInput('image symlink escapes extraction: ' + absolute)
    return path


def logical_lines(text, source):
    """Pie init tokens: double quotes may span lines; single quotes are literal.

    Unlike shell syntax, # begins a comment only at a token boundary. See
    platform/system/core init/tokenizer.cpp, android-9.0.0_r61. Unterminated
    strings/continuations fail closed instead of silently discarding input.
    """
    tokens, word = [], []
    active = quoted = False
    line = start = 1
    indented = bool(text and text[0] in ' \t\r')
    i = 0
    while i < len(text):
        ch = text[i]
        if quoted:
            if ch == '"':
                quoted = False
            else:
                word.append(ch)
                line += ch == '\n'
            i += 1
            continue
        if ch == '"':
            quoted = active = True
            i += 1
            continue
        if ch == '\\':
            i += 1
            if i >= len(text):
                raise InvalidInput(source + ': unfinished continuation/escape')
            ch = text[i]
            if ch == '\r' and i + 1 < len(text) and text[i + 1] == '\n':
                i += 1
                ch = '\n'
            if ch == '\n':
                line += 1
                i += 1
                while i < len(text) and text[i] in ' \t':
                    i += 1
                continue
            word.append({'n': '\n', 'r': '\r', 't': '\t'}.get(ch, ch))
            active = True
            i += 1
            continue
        if ch == '#' and not active:
            while i < len(text) and text[i] != '\n':
                i += 1
            continue
        if ch in ' \t\r\n':
            if active:
                tokens.append(''.join(word))
                word = []
                active = False
            if ch == '\n':
                if tokens:
                    yield start, indented, tokens
                tokens = []
                line += 1
                start = line
                indented = i + 1 < len(text) and text[i + 1] in ' \t\r'
            i += 1
            continue
        word.append(ch)
        active = True
        i += 1
    if quoted:
        raise InvalidInput(f'{source}:{start}: unterminated double quote')
    if active:
        tokens.append(''.join(word))
    if tokens:
        yield start, indented, tokens


def properties(root):
    result, conflicts = {}, []
    for name in ('/default.prop', '/prop.default', '/system/etc/prop.default',
                 '/system/build.prop', '/vendor/build.prop', '/system/vendor/build.prop'):
        path = rooted(root, name)
        if not path.is_file():
            continue
        for number, line in enumerate(path.read_text().splitlines(), 1):
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = (part.strip() for part in line.split('=', 1))
            if key in result and result[key] != value and (key.startswith('ro.adb.') or key in ('ro.secure', 'ro.hardware', 'ro.zygote')):
                conflicts.append(f'{name}:{number}: conflicting {key}')
            result[key] = value
    return result, conflicts


def imported_services(root, props):
    """Read /init.rc import closure and Pie's auto-imported init directories.

    Do not count unimported root RC files as evidence that a service exists.
    Missing non-service imports are still reported: this is a complete-input
    checker, not an image extractor. Nonliteral imports fail closed.
    """
    queue = ['/init.rc']
    for directory in ('/system/etc/init', '/vendor/etc/init', '/odm/etc/init'):
        base = rooted(root, directory)
        if base.is_dir():
            queue.extend('/' + p.relative_to(root).as_posix()
                         for p in sorted(base.glob('*.rc')))
    seen, definitions, problems = set(), [], []
    while queue:
        name = queue.pop(0)
        if name in seen:
            continue
        if len(seen) >= 512:
            raise InvalidInput('init import limit exceeded')
        seen.add(name)
        path = rooted(root, name)
        if path.is_dir():
            queue.extend('/' + p.relative_to(root).as_posix()
                         for p in sorted(path.glob('*.rc')))
            continue
        if not path.is_file():
            problems.append('missing imported RC: ' + name)
            continue
        current = None
        for number, indented, tokens in logical_lines(path.read_text(), name):
            if not indented:
                current = None
                if tokens[0] == 'import':
                    if len(tokens) != 2:
                        raise InvalidInput(f'{name}:{number}: malformed import')
                    target = re.sub(r'\$\{([^}]+)\}', lambda m: props.get(m[1], m[0]), tokens[1])
                    if '$' in target or any(c in target for c in '*?['):
                        problems.append(f'{name}:{number}: unresolved import {target}')
                    else:
                        rooted(root, target)
                        queue.append(target)
                elif tokens[0] == 'service':
                    if len(tokens) < 3:
                        raise InvalidInput(f'{name}:{number}: malformed service')
                    current = dict(name=tokens[1], argv=tokens[2:], options=[],
                                   source=name, line=number)
                    definitions.append(current)
            elif current is not None:
                current['options'].append(tokens)
    return definitions, sorted(seen), problems


def check(root, observation, phase='bind'):
    root = Path(root).resolve()
    if not root.is_dir() or root == Path('/'):
        raise InvalidInput('supply an extracted rootfs directory, never /')
    if phase not in ('start', 'bind') or not isinstance(observation, dict):
        raise InvalidInput('invalid phase or observation object')
    props = observation.get('properties', {})
    if not isinstance(props, dict) or not all(isinstance(k, str) and isinstance(v, str)
                                             for k, v in props.items()):
        raise InvalidInput('properties must map strings to strings')
    image_props, conflicts = properties(root)
    # Explicit observations may supply bootloader properties missing from files.
    # Image values win; observations cannot override an insecure image property.
    import_props = dict(props)
    import_props.update(image_props)
    defs, imports, import_errors = imported_services(root, import_props)
    errors = []

    def require(ok, code, detail):
        if not ok:
            errors.append(dict(code=code, detail=detail))

    for item in conflicts:
        require(False, 'PROPERTY_CONFLICT', item)
    for item in import_errors:
        require(False, 'INIT_IMPORT_INCOMPLETE', item)
    adb = [d for d in defs if d['name'] == 'adbd']
    require(len(adb) == 1, 'ADBD_SERVICE',
            f'expected one imported service adbd, found {len(adb)}')
    binary_sha = None
    if len(adb) == 1:
        definition = adb[0]
        options = definition['options']
        require(definition['argv'] == ['/system/bin/adbd'], 'ADBD_EXEC',
                'candidate requires /system/bin/adbd without root options')
        require(['disabled'] in options, 'ADBD_DISABLED', 'adbd must be disabled until opt-in')
        require(['socket', 'adbd', 'stream', '660', 'system', 'system'] in options,
                'ADBD_AUTH_SOCKET', 'missing standard system-owned adbd authentication socket')
        require(['seclabel', 'u:r:adbd:s0'] in options, 'ADBD_DOMAIN', 'adbd SELinux domain required')
        require(not any(o and o[0] in ('oneshot', 'override') for o in options),
                'ADBD_LIFECYCLE', 'oneshot/override adbd service is not supported')
        for key in ('socket', 'seclabel', 'user', 'group', 'disabled'):
            require(sum(bool(o) and o[0] == key for o in options) <= 1,
                    'ADBD_OPTIONS', 'duplicate adbd option: ' + key)
        require(not any(o and o[0] == 'setenv' for o in options),
                'ADBD_ENV', 'custom adbd environment requires a separate audit')
        binary = rooted(root, '/system/bin/adbd')
        require(binary.is_file(), 'ADBD_BINARY', 'adbd binary absent from extracted image')
        if binary.is_file():
            mode = binary.stat().st_mode
            head = binary.read_bytes()
            require(stat.S_ISREG(mode) and bool(mode & 0o111) and not mode & 0o022,
                    'ADBD_MODE', 'adbd must be executable and not group/world writable')
            require(len(head) >= 52 and head[:7] == b'\x7fELF\x01\x01\x01'
                    and head[18:20] == b'\x28\x00', 'ADBD_ELF', 'adbd must be ARM32 LE ELF')
            binary_sha = hashlib.sha256(head).hexdigest()
    for key in ('ro.adb.secure', 'ro.secure'):
        require(image_props.get(key) == '1', 'IMAGE_AUTH', f'image {key} must explicitly equal 1')
        require(props.get(key) == '1', 'RUNTIME_AUTH', f'observed {key} must explicitly equal 1')
    require(props.get('ro.z1.usb.candidate') == '1', 'OPT_IN', 'explicit candidate opt-in absent')
    require(props.get('sys.z1.usb.candidate.request') == 'acm,adb', 'REQUEST', 'ACM+ADB request absent')
    require(props.get('service.adb.root') != '1', 'ROOT_ADB', 'root ADB request is forbidden')
    require(props.get('ro.kernel.qemu') != '1', 'EMULATOR', 'emulator authentication path forbidden')
    for key in ('service.adb.tcp.port', 'persist.adb.tcp.port'):
        require(props.get(key, '') in ('', '0', '-1'), 'TCP_ADB', key + ' enables network ADB')
    require(observation.get('selinux_enforcing') is True, 'SELINUX', 'enforcing policy is required')
    require(observation.get('configfs_mounted') is True, 'CONFIGFS', '/config must be configfs')
    require(props.get('init.svc.z1_acm_log', '') in ('', 'stopped'),
            'ACM_OWNER', 'current ACM logger must not compete for the UDC')
    owners = observation.get('bound_gadgets')
    if not isinstance(owners, dict) or not all(isinstance(k, str) and isinstance(v, str)
                                              for k, v in owners.items()):
        raise InvalidInput('bound_gadgets must explicitly map gadget names to UDC strings')
    require(not any(owners.values()), 'UDC_BUSY', 'a gadget already owns the UDC; never steal ACM')
    udcs = observation.get('udcs')
    if not isinstance(udcs, list) or not all(isinstance(u, dict) for u in udcs):
        raise InvalidInput('udcs must explicitly list observed controller objects')
    controller = None
    require(len(udcs) == 1, 'UDC_COUNT', 'exactly one controller is required')
    if len(udcs) == 1:
        name = udcs[0].get('name')
        known = isinstance(name, str) and MUSB.fullmatch(name) and udcs[0].get('driver') == 'musb-hdrc'
        require(bool(known), 'UDC_UNKNOWN', 'controller name and driver must identify MUSB')
        if known:
            controller = name
    if phase == 'start':
        require(props.get('init.svc.adbd') == 'stopped', 'ADBD_STOPPED',
                'known adbd service must be stopped before preparing FunctionFS')
        # Deliberately no ep0, ffs mount or readiness dependency here.
    else:
        require(props.get('init.svc.adbd') == 'running', 'ADBD_RUNNING', 'adbd is not running')
        require(props.get('sys.z1.usb.candidate.prepared') == '1', 'PREPARED', 'candidate not prepared')
        require(controller is not None and props.get('sys.z1.usb.candidate.controller') == controller,
                'CONTROLLER_CHANGED', 'prepared controller differs from sole observed MUSB')
        require(observation.get('auth_socket') is True, 'AUTH_SOCKET', 'live adbd auth socket absent')
        require(props.get('sys.usb.ffs.ready') == '1', 'FFS_PROPERTY', 'daemon descriptors not ready')
        require(observation.get('ffs_kernel_ready') == '1', 'FFS_KERNEL', 'kernel descriptors not ready')
        require(observation.get('ep0_type') == 'regular', 'FFS_EP0', 'FunctionFS ep0 must be a regular file')
        require(observation.get('ffs_mount') == dict(source='adb', fstype='functionfs', target='/dev/usb-ffs/adb'),
                'FFS_MOUNT', 'ADB FunctionFS mount missing or mismatched')
    return dict(status='BLOCKED' if errors else 'OFFLINE_PREDICATES_PASS',
                deployment='NOT_DEPLOYED', deployed=False, device_runtime_verified=False,
                adb_working=False, hardware_authorized=False, phase=phase,
                may_start_adbd_in_model=not errors and phase == 'start',
                may_bind_in_model=not errors and phase == 'bind', controller=controller,
                adbd_sha256=binary_sha, adbd_services=adb, imported_rc=imports, errors=errors,
                unverified=['adbd dependency/SELinux closure', 'actual compiled authentication behavior',
                            'USB enumeration', 'unknown-host rejection', 'authorized-host acceptance'])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rootfs', type=Path, required=True)
    parser.add_argument('--state', type=Path, required=True, help='explicit offline observation JSON')
    parser.add_argument('--phase', choices=('start', 'bind'), default='bind')
    args = parser.parse_args(argv)
    try:
        report = check(args.rootfs, json.loads(args.state.read_text()), args.phase)
    except (OSError, ValueError, UnicodeError) as exc:
        print(json.dumps(dict(status='INVALID_INPUT', deployment='NOT_DEPLOYED',
                              adb_working=False, error=str(exc)), indent=2))
        return 2
    print(json.dumps(report, indent=2))
    return 1 if report['errors'] else 0


if __name__ == '__main__':
    sys.exit(main())

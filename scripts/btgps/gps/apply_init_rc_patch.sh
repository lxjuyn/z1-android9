#!/bin/sh
# Offline host tool; requires Python 3. Never invoke on a live Android root.
# Usage: ./apply_init_rc_patch.sh /path/to/unpacked/ramdisk
set -eu
[ "$#" -eq 1 ] || { echo "usage: $0 <offline-ramdisk-tree>" >&2; exit 2; }
command -v python3 >/dev/null 2>&1 || { echo 'ERROR: Python 3 is required' >&2; exit 1; }
exec python3 - "$1" <<'PY'
import os
import re
import shutil
import stat
import sys
import tempfile
from pathlib import Path

OLD, NEW = b'z1_mnld', b'mnld'
PROPERTY = b'init.svc.z1_mnld'


def fail(message):
    raise ValueError(message)


def tokens(line):
    """Return decoded tokens and byte spans; leave comments outside spans."""
    result, i = [], 0
    while i < len(line):
        if line[i] in b' \t\r\n':
            i += 1
            continue
        if line[i] == 35:  # comment beginning at a token boundary
            break
        start, value, quoted = i, bytearray(), False
        while i < len(line):
            c = line[i]
            if not quoted and c in b' \t\r\n':
                break
            if c == 34:
                quoted = not quoted
                i += 1
            elif c == 92:
                # Avoid guessing Android escape/continuation semantics.
                fail('backslash escapes/continuations are unsupported; file left untouched')
            else:
                value.append(c)
                i += 1
        if quoted:
            fail('unterminated quoted token')
        result.append((bytes(value), start, i))
    return result


def transform(data):
    """Validate one target declaration and rename only known init references."""
    if b'\0' in data:
        fail('NUL byte in init rc')
    declarations, output, replacements = [], [], 0
    for number, line in enumerate(data.splitlines(keepends=True), 1):
        ts = tokens(line)
        if not ts:
            output.append(line)
            continue
        values = [t[0] for t in ts]
        changes = {}

        def rename(index, before=OLD, after=NEW):
            nonlocal replacements
            _, start, end = ts[index]
            raw = changes.get(index, line[start:end])
            if before not in raw:
                fail('unsupported spelling of target on line %d' % number)
            changes[index] = raw.replace(before, after)
            replacements += 1

        if values[0] == b'service' and len(values) > 1 and values[1] in (OLD, NEW):
            if len(values) < 3 or values[2] != b'/vendor/bin/mnld':
                fail('target service has unexpected executable on line %d' % number)
            declarations.append(values[1])
            if values[1] == OLD:
                rename(1)

        offset = 1 if values[0] == b'onrestart' else 0
        args = values[offset:]
        if args:
            command = args[0]
            if command in (b'start', b'stop', b'restart', b'enable', b'exec_start'):
                pos = offset + 1
                if command == b'restart' and len(args) > 1 and args[1] == b'--only-if-running':
                    pos += 1
                if OLD in values[pos:]:
                    if len(values) != pos + 1 or values[pos] != OLD:
                        fail('ambiguous service command on line %d' % number)
                    rename(pos)
            elif command == b'setprop' and len(args) > 1 and args[1] in (
                    b'ctl.start', b'ctl.stop', b'ctl.restart', b'ctl.oneshot_on', b'ctl.oneshot_off'):
                if OLD in args[2:]:
                    if len(args) != 3 or args[2] != OLD:
                        fail('ambiguous ctl reference on line %d' % number)
                    rename(offset + 2)
            elif command == b'wait_for_prop' and len(args) > 1 and args[1] == PROPERTY:
                if len(args) != 3:
                    fail('ambiguous wait_for_prop on line %d' % number)
                rename(offset + 1, PROPERTY, b'init.svc.mnld')
            elif command in (b'exec', b'exec_background') and any(
                    re.search(rb'(?<![\w./-])z1_mnld(?![\w./-])', value) for value in args[1:]):
                fail('embedded command references old service on line %d; edit it explicitly' % number)

        for index, (value, start, end) in enumerate(ts):
            if values[0] == b'on' and value.startswith(b'property:' + PROPERTY + b'='):
                rename(index, b'property:' + PROPERTY + b'=', b'property:init.svc.mnld=')
            raw = changes.get(index, line[start:end])
            if b'${' + PROPERTY + b'}' in raw:
                rename(index, b'${' + PROPERTY + b'}', b'${init.svc.mnld}')
            raw = changes.get(index, line[start:end])
            if re.search(rb'init\.svc\.z1_mnld(?![\w.-])', raw):
                fail('unsupported old service property reference on line %d' % number)
        for index in sorted(changes, reverse=True):
            _, start, end = ts[index]
            line = line[:start] + changes[index] + line[end:]
        output.append(line)
    if len(declarations) != 1:
        fail('expected exactly one mnld/z1_mnld service; found %d (conflict, duplicate, or no match)' % len(declarations))
    return b''.join(output), replacements


def identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def main():
    root = Path(sys.argv[1])
    if root.is_symlink() or not root.is_dir() or root.resolve() == Path('/'):
        fail('expected an offline ramdisk directory, not a symlink or live root')
    target = root / 'init.z1.network.rc'
    info = target.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        fail('target must be a regular, non-symlink, single-link file')
    original = target.read_bytes()
    expected, count = transform(original)
    verified, remaining = transform(expected)
    if verified != expected or remaining:
        fail('generated content failed exact service/reference validation')
    if expected == original:
        if count:
            fail('expected replacements made no change')
        if identity(target.lstat()) != identity(info) or target.read_bytes() != original:
            fail('target changed during validation')
        print('OK: already renamed; exactly one mnld service and no old supported references')
        return
    if not count:
        fail('changed content without a verified replacement')
    temp = None
    try:
        fd, temp = tempfile.mkstemp(prefix='.init.z1.network.rc.', dir=str(root))
        with os.fdopen(fd, 'wb') as stream:
            stream.write(expected)
            stream.flush()
            os.fsync(stream.fileno())
            current = os.fstat(stream.fileno())
            if (current.st_uid, current.st_gid) != (info.st_uid, info.st_gid):
                os.fchown(stream.fileno(), info.st_uid, info.st_gid)
        shutil.copystat(target, temp, follow_symlinks=False)
        if Path(temp).read_bytes() != expected:
            fail('temporary output did not contain the expected changes')
        if identity(target.lstat()) != identity(info) or target.read_bytes() != original:
            fail('target changed before replacement')
        os.replace(temp, target)
        actual = target.read_bytes()
        if actual != expected:
            fail('write read-back mismatch; rename was not verified')
        reread, remaining = transform(actual)
        if reread != actual or remaining:
            fail('written service/reference validation failed')
        print('OK: renamed and read back exactly; %d token replacements. Repack the offline ramdisk.' % count)
    finally:
        if temp is not None and os.path.exists(temp):
            os.unlink(temp)


try:
    main()
except (OSError, ValueError) as error:
    print('ERROR: %s' % error, file=sys.stderr)
    sys.exit(1)
PY

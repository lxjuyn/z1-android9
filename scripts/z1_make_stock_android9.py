#!/usr/bin/env python3
"""Build a diagnostic Android9 boot image with the exact A7/factory kernel.

No flashing. Never append a mainline DTB or replace the original kernel body.
The matching Binder32 system image must be built separately.
"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import tempfile

from z1_add_bootdiag import entries, encode

KERNEL_SHA = 'f9afaa2a38d64e5a6d6877ccb73ede2250b7efe6389fa60e66ee500cb885cc07'
LIMIT = 6291456
REPO_ROOT = Path(__file__).resolve().parent.parent


def load_mount_guard(prebuilt):
    if prebuilt is not None:
        data = prebuilt.read_bytes()
    else:
        with tempfile.TemporaryDirectory(prefix='z1-stock-mount-guard-') as directory:
            output = Path(directory) / 'z1_stock_mount_guard'
            subprocess.run(['bash', str(REPO_ROOT / 'stock/build_mount_guard.sh'), str(output)],
                           check=True, timeout=120)
            data = output.read_bytes()
    elf_static_arm(data)
    if b'Z1 stock mount guard' not in data or b'sys.z1.stock_mount_ready' not in data:
        raise ValueError('mount guard lacks stock readiness protocol')
    return data


def digest(data):
    return hashlib.sha256(data).hexdigest()


def elf_static_arm(data):
    if len(data) < 52 or data[:7] != b'\x7fELF\x01\x01\x01' or struct.unpack_from('<H', data, 18)[0] != 40:
        raise ValueError('expected little-endian ARM32 ELF')
    if struct.unpack_from('<H', data, 16)[0] != 2:
        raise ValueError('expected an executable ELF, not a shared object')
    phoff = struct.unpack_from('<I', data, 28)[0]
    phsize, phnum = struct.unpack_from('<HH', data, 42)
    if phsize != 32 or not phnum or phoff < 52 or phoff+phnum*phsize > len(data):
        raise ValueError('invalid ELF program header table')
    loaded = False
    for i in range(phnum):
        kind, offset, _, _, filesz, memsz, _, _ = struct.unpack_from('<8I', data, phoff+i*phsize)
        if kind in (2, 3):
            raise ValueError('ramdisk executable cannot use PT_DYNAMIC or PT_INTERP')
        if kind == 1:
            loaded = True
            if filesz > memsz or offset+filesz > len(data):
                raise ValueError('truncated ELF load segment')
    if not loaded:
        raise ValueError('executable ELF lacks a load segment')


def ramdisk_key(name):
    key = name.decode()
    while key.startswith('./'):
        key = key[2:]
    if not key or key.startswith('/') or (key != '.' and any(p in ('', '.', '..') for p in key.split('/'))):
        raise ValueError('unsafe or ambiguous ramdisk path: '+repr(name))
    return key


def ramdisk_index(records):
    result = {}
    for name, fields, data in records:
        key = ramdisk_key(name)
        if key in result:
            raise ValueError('duplicate ramdisk path: '+key)
        result[key] = (name, fields, data)
    return result


def check_boot_executable(index, key, expected, exact_mode=None):
    if key not in index:
        raise ValueError('missing ramdisk executable: /'+key)
    _, fields, data = index[key]
    mode = fields[1]
    if mode & 0o170000 != 0o100000 or fields[2:5] != [0, 0, 1]:
        raise ValueError('/'+key+' must be a root:root regular file with one link')
    if mode & 0o7022 or mode & 0o500 != 0o500 or (exact_mode is not None and mode != exact_mode):
        raise ValueError('unsafe executable permissions for /'+key)
    if not data or fields[6] != len(data) or data != expected:
        raise ValueError('ramdisk executable content mismatch: /'+key)
    elf_static_arm(data)


def mount_guard_fields(fields, inode):
    fields = fields.copy()
    fields[0] = inode
    fields[1] = 0o100755
    fields[2:5] = [0, 0, 1]
    fields[7:11] = [0, 0, 0, 0]
    return fields


def parse_boot(data):
    if data[:8] != b'ANDROID!':
        raise ValueError('invalid boot magic')
    ks, ka, rs, ra, ss, sa, ta, page, dt = struct.unpack_from('<9I', data, 8)
    if page != 2048 or ss or dt or (ka, ra) != (0x10008000, 0x11000000):
        raise ValueError('expected A7 MTK v0 boot layout, without DTB')
    k = data[page:page+ks]
    off = page + ((ks+page-1)//page)*page
    r = data[off:off+rs]
    if len(k) != ks or len(r) != rs:
        raise ValueError('truncated boot image')
    if k[:4] != b'\x88\x16\x88\x58' or k[8:14] != b'KERNEL':
        raise ValueError('missing MTK KERNEL header')
    if struct.unpack_from('<I', k, 4)[0] != len(k)-512 or digest(k[512:]) != KERNEL_SHA:
        raise ValueError('kernel is not the verified exact factory/A7 3.4.67 payload')
    return page, k, r, off


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--a7-boot', type=Path, required=True)
    ap.add_argument('--factory-boot', type=Path, required=True)
    ap.add_argument('--ramdisk', type=Path, required=True)
    ap.add_argument('--helper', type=Path, required=True)
    ap.add_argument('--mount-guard', type=Path,
                    help='reuse a static ARM guard; default builds stock/ source with Z1_GUARD_CC')
    ap.add_argument('--policy', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    if args.output.exists():
        raise ValueError('output already exists')
    base = args.a7_boot.read_bytes()
    page, kernel, oldrd, offset = parse_boot(base)
    _, factory, _, _ = parse_boot(args.factory_boot.read_bytes())
    if kernel != factory:
        raise ValueError('A7 and factory kernel headers/payload must match exactly')
    helper = args.helper.read_bytes()
    elf_static_arm(helper)
    if b'--acm-stock' not in helper or b'--graphics-mode' not in helper or b'--stock-partitions' not in helper:
        raise ValueError('helper lacks stock transport/display takeover')
    mount_guard = load_mount_guard(args.mount_guard)
    policy = args.policy.read_bytes()
    if len(policy) < 32 or struct.unpack_from('<I', policy, 0)[0] != 0xf97cff8c:
        raise ValueError('invalid SELinux binary policy')
    ident_size = struct.unpack_from('<I', policy, 4)[0]
    version = struct.unpack_from('<I', policy, 8+ident_size)[0]
    if version != 26:
        raise ValueError('stock policy must use binary version 26')
    src = entries(gzip.decompress(args.ramdisk.read_bytes()))
    originals = ramdisk_index(src)
    if 'init' not in originals:
        raise ValueError('ramdisk lacks /init')
    check_boot_executable(originals, 'init', originals['init'][2])
    if 'init.z1.rc' not in originals:
        raise ValueError('ramdisk does not contain the stock board init actions')
    board_rc = (Path(__file__).resolve().parent.parent / 'stock/init.z1.rc').read_bytes()
    gate = (b'    exec u:r:init:s0 root root -- /z1_stock_mount_guard\n'
            b'    wait_for_prop sys.z1.stock_mount_ready 1\n')
    if board_rc.count(gate) != 1 or board_rc.count(b'    setprop sys.z1.stock_mount_ready 0\n') != 1:
        raise ValueError('stock board init lacks fail-closed mount gate')
    post_fs = board_rc.split(b'on post-fs\n', 1)[1].split(b'\non ', 1)[0]
    executable = [line for line in post_fs.splitlines() if line.strip() and not line.lstrip().startswith(b'#')]
    if executable[:2] != gate.splitlines():
        raise ValueError('mount gate must precede every stock post-fs command')
    fbcon_script = (Path(__file__).resolve().parent.parent / 'stock/z1_fbcon_takeover.sh').read_bytes()
    display_script = (Path(__file__).resolve().parent.parent / 'stock/z1_display_takeover.sh').read_bytes()
    if (b'/z1_display_takeover.sh' not in board_rc or
            b'/z1_bootdiag --graphics-mode' not in display_script or
            b'/system/bin/mknod -m 0600 /dev/tty0 c 4 0' not in display_script or
            b'/z1_fbcon_takeover.sh' not in display_script or
            b'/sys/class/vtconsole' not in fbcon_script):
        raise ValueError('stock init or framebuffer-console fallback is incomplete')
    original_rc = originals['init.rc'][2]
    generic_import = b'import /init.${ro.hardware}.rc'
    stock_import = b'import /init.z1.rc'
    if original_rc.count(generic_import) == 1 and original_rc.count(stock_import) == 0:
        rc = original_rc.replace(generic_import, stock_import)
    elif original_rc.count(generic_import) == 0 and original_rc.count(stock_import) == 1:
        # Rebuilding from an already validated S1 image is useful for focused
        # ramdisk fixes. Keep its vendor init import and board selection intact.
        rc = original_rc
    else:
        raise ValueError('unexpected init.rc board import')
    rc = rc.replace(b'    start hwservicemanager\n', b'').replace(b'    start vndservicemanager\n', b'')
    # Diagnostic ACM is owned by the early static logger. Prevent generic USB
    # property actions from changing functions when old persisted settings load.
    usb = originals['init.usb.rc'][2]
    retained = []
    keep = True
    for line in usb.splitlines(keepends=True):
        if line.startswith(b'on ') or line.startswith(b'service '):
            keep = not line.startswith(b'on property:')
        if keep:
            retained.append(line)
    replacements = {
        'init.rc': rc, 'init.usb.rc': b''.join(retained), 'sepolicy': policy,
        'init.z1.rc': board_rc,
        'z1_fbcon_takeover.sh': fbcon_script,
        'z1_display_takeover.sh': display_script,
        'z1_stock_kernel': b'Z1 stock S1: exact factory/A7 3.4.67; diagnostic permissive policy26\n',
        'z1_bootdiag': helper,
        'z1_stock_mount_guard': mount_guard,
        'ueventd.mt6572.rc': originals['ueventd.z1.rc'][2],
    }
    records = []
    maxino = max(f[0] for _, f, _ in src)
    nextino = maxino
    for name, fields, data in src:
        key = ramdisk_key(name)
        if key == 'TRAILER!!!':
            for key, content in replacements.items():
                nextino += 1
                mode = 0o100755 if key in ('z1_bootdiag', 'z1_stock_mount_guard', 'z1_fbcon_takeover.sh',
                                           'z1_display_takeover.sh') else 0o100644
                f = [nextino, mode, 0, 0, 1, 0, len(content), 0, 0, 0, 0, 0, 0]
                records.append((key.encode(), f, content))
            records.append((name, fields, data))
        elif key in replacements:
            if key in ('z1_stock_mount_guard', 'z1_fbcon_takeover.sh', 'z1_display_takeover.sh'):
                fields = fields.copy()
                fields[1] = 0o100755
            if key == 'z1_stock_mount_guard':
                # An input symlink/hardlink/non-root entry cannot define the
                # metadata of the newly supplied trusted static executable.
                nextino += 1
                fields = mount_guard_fields(fields, nextino)
            records.append((name, fields, replacements.pop(key)))
        else:
            records.append((name, fields, data))
    archive = b''.join(encode(*item) for item in records)
    archive += b'\0' * (-len(archive) % 512)
    final_index = ramdisk_index(entries(archive))
    check_boot_executable(final_index, 'init', originals['init'][2])
    check_boot_executable(final_index, 'z1_stock_mount_guard', mount_guard, 0o100755)
    final = {key: data for key, (_, _, data) in final_index.items()}
    assert final['sepolicy'] == policy and final['z1_bootdiag'] == helper
    assert final['z1_stock_mount_guard'] == mount_guard and gate in final['init.z1.rc']
    assert final['init.z1.rc'] == board_rc
    assert final['z1_fbcon_takeover.sh'] == fbcon_script
    assert b'/system/bin/sh /z1_display_takeover.sh' in final['init.z1.rc']
    assert b'/z1_bootdiag --graphics-mode' in final['z1_display_takeover.sh']
    assert final['init'] == originals['init'][2]
    assert b'start hwservicemanager' not in final['init.rc']
    assert b'on property:' not in final['init.usb.rc']
    body = gzip.compress(archive, compresslevel=9, mtime=0)
    if oldrd[:4] != b'\x88\x16\x88\x58' or oldrd[8:14] != b'ROOTFS':
        raise ValueError('A7 ROOTFS header missing')
    mtk = bytearray(oldrd[:512])
    struct.pack_into('<I', mtk, 4, len(body))
    rd = bytes(mtk)+body
    header = bytearray(base[:page])
    struct.pack_into('<I', header, 16, len(rd))
    sha = hashlib.sha1()
    for payload in (kernel, rd, b''):
        sha.update(payload)
        sha.update(struct.pack('<I', len(payload)))
    header[576:608] = sha.digest()+b'\0'*12
    def padded(data):
        return data+b'\0'*(-len(data)%page)
    image = bytes(header)+padded(kernel)+padded(rd)
    if len(image) > LIMIT:
        raise ValueError('boot image exceeds 6MiB partition')
    _, checkk, checkr, _ = parse_boot(image)
    assert checkk == kernel and checkr == rd
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(image)
    report = dict(result='STATIC_STOCK_BOOT_PASS', device_boot_verified=False,
        kernel_version='3.4.67 (factory/A7 exact payload)', kernel_sha256=KERNEL_SHA,
        boot_bytes=len(image), boot_sha256=digest(image), ramdisk_sha256=digest(rd),
        policy_version=version, policy_sha256=digest(policy), helper_sha256=digest(helper),
        mount_guard_sha256=digest(mount_guard),
        mount_guard_source_sha256=digest((REPO_ROOT / 'stock/z1_stock_mount_guard.c').read_bytes()),
        mount_guard_origin='provided ELF' if args.mount_guard else 'compiled repository stock source',
        executable_metadata_verified=['init: root:root regular, one link, owner rx, no special/group/world write bits',
                                      'z1_stock_mount_guard: root:root regular, one link, 0755'],
        mount_gate='post-fs wait_for_prop; runtime unverified',
        unchanged_ramdisk_files=sum(final.get(k) == d for k, (_, _, d) in originals.items()),
        risks=['experimental native HIDL passthrough; Java HIDL services unavailable',
               'policy26 permissive domains and allow_unknown; diagnostic build',
               'software graphics; A7 Mali binary ABI not yet adapted'])
    args.output.with_suffix('.validation.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()

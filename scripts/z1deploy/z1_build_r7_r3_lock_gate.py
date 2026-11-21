#!/usr/bin/env python3
"""Grant the R7 R3 composer access to its existing backlight gate."""
import argparse
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import z1_prepare_recovery_boot as boot

BASE_SHA = '5baddbade8fb3b50e53220370d1eb11f1690a428af2690869e1e44c7e4e8441b'
ANCHOR = b'    chown system system /sys/class/leds/lcd-backlight/brightness\n'
GATE = b'    chown system system /sys/class/leds/lcd-backlight/display_blank\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('base', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    data = args.base.read_bytes()
    boot.require(boot.sha(data) == BASE_SHA, 'not the released R7 R3 test boot')
    header, kh, zimage, dtb, root = boot.split_boot(data)
    body, records = boot.cpio(root)
    previous = boot.contents(body, records)
    init = previous['init.rc']
    boot.require(init.count(ANCHOR) == 1 and GATE not in init and
                 init.index(ANCHOR) < init.index(b'    class_start hal\n'),
                 'unexpected backlight ownership or HAL startup sequence')
    replacement = init.replace(ANCHOR, ANCHOR + GATE)
    patched_root = boot.edit_rootfs(root, {'init.rc': replacement})
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'appended.dtb'
        path.write_bytes(dtb)
        start = int(boot.dt_read(path, '/chosen', 'linux,initrd-start'), 16)
        boot.require(start == 0x84100000 and int(boot.dt_read(
            path, '/chosen', 'linux,initrd-end'), 16) == start + len(root) - 512,
            'base initrd contract mismatch')
        end = start + len(patched_root) - 512
        subprocess.run(['fdtput', '-t', 'x', str(path), '/chosen',
                        'linux,initrd-end', f'{end:x}'], check=True)
        patched_dtb = path.read_bytes()
    image = boot.make_image(header, kh, zimage, patched_dtb, patched_root)
    _, _, got_kernel, got_dtb, got_root = boot.split_boot(image)
    boot.require((got_kernel, got_dtb, got_root) == (zimage, patched_dtb, patched_root),
                 'boot split readback mismatch')
    new_body, new_records = boot.cpio(got_root)
    current = boot.contents(new_body, new_records)
    boot.require(list(current) == list(previous) and
                 current == {**previous, 'init.rc': replacement}, 'unexpected cpio change')
    for before, after in zip(records, new_records):
        if before['name'] == 'init.rc':
            boot.require(before['fields'][:6] == after['fields'][:6], 'init.rc metadata changed')
        else:
            boot.require(body[before['pos']:before['file_end']] ==
                         new_body[after['pos']:after['file_end']], 'unchanged record differs')
    boot.require(len(image) <= 0x600000, 'boot exceeds partition size')
    with args.output.open('xb') as out:
        out.write(image)
    print(f'{boot.sha(image)}  {args.output} ({len(image)} bytes)')


if __name__ == '__main__':
    main()

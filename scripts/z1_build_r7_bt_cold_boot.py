#!/usr/bin/env python3
"""Remove the automatic Wi-Fi probe from the installed R7 core-wake boot."""
import argparse
import hashlib
from pathlib import Path
import subprocess
import tempfile

from z1_prepare_recovery_boot import (
    contents, cpio, dt_read, edit_rootfs, make_image, require, sha, split_boot,
)

BASE_SHA = '09b2c74d7c0de0c944ba32f041a5fd92a054df3df4debe40ae8434cfe38030d4'
SYSTEM_SHA = '10decdbb68998ff6abf333a9df42d7339d5a6f219d9959ffee28011f53f320d0'
RC = 'init.z1.rc'
PROBE = b'    exec_background u:r:init:s0 root root -- /system/bin/sh /z1_wifi_probe.sh\n'


def build(base):
    require(sha(base) == BASE_SHA, 'expected the installed core-wake boot')
    header, kernel_header, kernel, original_dtb, rootfs = split_boot(base)
    entries = contents(*cpio(rootfs))
    require(entries[RC].count(PROBE) == 1 and 'z1_wifi_probe.sh' in entries,
            'unexpected automatic Wi-Fi probe layout')
    rc = entries[RC].replace(PROBE, b'', 1)
    updated_rootfs = edit_rootfs(rootfs, {RC: rc})

    with tempfile.TemporaryDirectory(prefix='z1-r7-bt-cold-') as scratch:
        dtb = Path(scratch) / 'appended.dtb'
        dtb.write_bytes(original_dtb)
        start = int(dt_read(dtb, '/chosen', 'linux,initrd-start'), 16)
        old_end = int(dt_read(dtb, '/chosen', 'linux,initrd-end'), 16)
        require(old_end == start + len(rootfs) - 512, 'wrong base initrd bounds')
        new_end = start + len(updated_rootfs) - 512
        command = ['fdtput', '-t', 'x', str(dtb), '/chosen', 'linux,initrd-end']
        subprocess.run([*command, f'{new_end:x}'], check=True)
        require(int(dt_read(dtb, '/chosen', 'linux,initrd-end'), 16) == new_end,
                'wrong output initrd bounds')
        updated_dtb = dtb.read_bytes()
        subprocess.run([*command, f'{old_end:x}'], check=True)
        require(dtb.read_bytes() == original_dtb, 'unexpected DTB change')

    output = make_image(header, kernel_header, kernel, updated_dtb, updated_rootfs)
    _, _, packed_kernel, packed_dtb, packed_rootfs = split_boot(output)
    require((packed_kernel, packed_dtb, packed_rootfs) ==
            (kernel, updated_dtb, updated_rootfs), 'boot payload readback mismatch')
    require(contents(*cpio(packed_rootfs)) == (entries | {RC: rc}),
            'unexpected ramdisk content change')
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ('base', 'system', 'output'):
        parser.add_argument(field, type=Path)
    args = parser.parse_args()
    with args.system.open('rb') as stream:
        require(hashlib.file_digest(stream, 'sha256').hexdigest() == SYSTEM_SHA,
                'expected the installed livegain system')
    image = build(args.base.read_bytes())
    with args.output.open('xb') as stream:
        stream.write(image)
    print(f'{sha(image)}  {args.output} ({len(image)} bytes)')


if __name__ == '__main__':
    main()

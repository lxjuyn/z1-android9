#!/usr/bin/env python3
"""Build an ART recovery boot for the audited R7 factory/Settings system."""
import argparse
import hashlib
from pathlib import Path
import subprocess
import tempfile

from z1_prepare_recovery_boot import (
    contents, cpio, dt_read, edit_rootfs, make_image, replace_once, require,
    sha, split_boot,
)

BASE_SHA = '1a5f3c1ac78afbc4112c92a754ac0d3fc7d690959992a349348df1e32463c768'
SYSTEM_SHA = 'f48322725f2f5f53d2d4b3c3b9d92379e0aea715b9d3b04ada03755381843cab'
CLEAN = b'''#!/system/bin/sh
log() { printf '<6>[z1art] %s\\n' "$*" > /dev/kmsg; }
for image in boot.art boot.oat boot.vdex boot-framework.art boot-framework.oat boot-framework.vdex; do
    if [ ! -s "/system/framework/arm/$image" ]; then
        log "missing system boot image member: $image"
        exit 1
    fi
done
for dir in /data/dalvik-cache /data/dalvik-cache/arm; do
    [ -d "$dir" ] || continue
    if ! rm -f "$dir"/system@framework@boot*.art "$dir"/system@framework@boot*.oat \\
               "$dir"/system@framework@boot*.vdex "$dir"/system@framework@boot*.rel \\
               "$dir"/boot*.art "$dir"/boot*.oat "$dir"/boot*.vdex "$dir"/boot*.rel; then
        log "boot image cache cleanup failed in $dir"
        exit 1
    fi
done
log 'boot image cache cleared; zygote will use system images'
setprop sys.z1.art_cache ready
'''


def build(base, system, radio_off=False):
    require(sha(base) == BASE_SHA, 'unexpected R7 boot hash')
    with system.open('rb') as stream:
        require(hashlib.file_digest(stream, 'sha256').hexdigest() == SYSTEM_SHA,
                'unexpected paired R7 system hash')
    header, kernel_header, zimage, original_dtb, rootfs = split_boot(base)
    body, records = cpio(rootfs)
    files = contents(body, records)
    rc = files['init.z1.rc'].decode()
    rc = replace_once(
        rc,
        '    # v38: 先按 dalvik-cache 命名把 boot 镜像种好（在 zygote 之前），再挂一个首启自愈看护\n'
        '    exec_background u:r:init:s0 root root -- /system/bin/sh /z1_art_seed.sh\n'
        '    exec_background u:r:init:s0 root root -- /system/bin/sh /z1_boot_heal.sh\n',
        '    # Clear generated ART boot images before init starts zygote.\n'
        '    setprop sys.z1.art_cache pending\n'
        '    exec u:r:init:s0 root root -- /system/bin/sh /z1_art_seed.sh\n'
        '    wait_for_prop sys.z1.art_cache ready\n',
    )
    zygote = replace_once(files['init.zygote32.rc'].decode(), ' -Xzygote ',
                          ' -Xzygote -Xnorelocate -Xnoimage-dex2oat ')
    replacements = {'init.z1.rc': rc.encode(), 'init.zygote32.rc': zygote.encode(),
                    'z1_art_seed.sh': CLEAN}
    if radio_off:
        # Keep the CONNSYS transport and WLAN modules out of this boot so the
        # board test covers a cold boot without wireless traffic or recovery.
        background = '    exec_background u:r:init:s0 root root -- /system/bin/sh '
        for script in ('/system/bin/z1_linux7_radio_init.sh', '/z1_radio_finish.sh',
                       '/z1_wifi_probe.sh'):
            rc = replace_once(rc, background + script + '\n', '')
        rc = replace_once(rc, 'on property:sys.z1.radio.modules_ready=1\n'
                         '    start z1_6620\n\n', '')
        rc = replace_once(rc, 'service z1_6620 /system/bin/6620_launcher -m 3 -p /system/etc/firmware/\n',
                          'service z1_6620 /system/bin/6620_launcher -m 3 -p /system/etc/firmware/\n'
                          '    disabled\n')
        network = replace_once(files['init.z1.network.rc'].decode(),
                               'on post-fs\n    exec u:r:init:s0 root root -- /system/bin/sh /z1_wifi_pre.sh\n\n', '')
        network = replace_once(network, 'on property:ro.persistent_properties.ready=true\n' +
                               background + '/system/bin/z1_linux7_radio_init.sh --bt-identity-only\n', '')
        replacements.update({'init.z1.rc': rc.encode(), 'init.z1.network.rc': network.encode()})
    new_rootfs = edit_rootfs(rootfs, replacements)
    with tempfile.TemporaryDirectory(prefix='z1-r7-art-') as scratch:
        dt = Path(scratch) / 'boot.dtb'
        dt.write_bytes(original_dtb)
        start = int(dt_read(dt, '/chosen', 'linux,initrd-start'), 16)
        old_end = int(dt_read(dt, '/chosen', 'linux,initrd-end'), 16)
        require(start == 0x84100000 and old_end == start + len(rootfs) - 512,
                'base initrd bounds do not match MTK payload')
        end = start + len(new_rootfs) - 512
        command = ['fdtput', '-t', 'x', str(dt), '/chosen', 'linux,initrd-end']
        subprocess.run([*command, f'{end:x}'], check=True)
        require(int(dt_read(dt, '/chosen', 'linux,initrd-end'), 16) == end,
                'incorrect initrd-end')
        new_dtb = dt.read_bytes()
        subprocess.run([*command, f'{old_end:x}'], check=True)
        require(dt.read_bytes() == original_dtb, 'unrelated DTB change')
    output = make_image(header, kernel_header, zimage, new_dtb, new_rootfs)
    _, _, packed_kernel, packed_dtb, packed_rootfs = split_boot(output)
    require(packed_kernel == zimage and packed_dtb == new_dtb, 'kernel readback mismatch')
    require(contents(*cpio(packed_rootfs)) == (files | replacements), 'ramdisk readback mismatch')
    return output


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('base', type=Path)
    cli.add_argument('system', type=Path)
    cli.add_argument('output', type=Path)
    cli.add_argument('--radio-off', action='store_true', help='isolate CONNSYS for a cold boot test')
    args = cli.parse_args()
    image = build(args.base.read_bytes(), args.system, args.radio_off)
    with args.output.open('xb') as stream:
        stream.write(image)
    print(f'{sha(image)}  {args.output} ({len(image)} bytes)')


if __name__ == '__main__':
    main()

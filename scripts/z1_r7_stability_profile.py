#!/usr/bin/env python3
"""Build an R7 test pair with optional GNSS deferred and the corrected mic route."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET

from release import z1_build_driver_candidate as system
import z1_build_adaptation_boot as boot
from z1_build_adaptation_boot import check_arm
from z1_prepare_recovery_boot import split_boot, cpio, contents, sha

HERE = Path(__file__).resolve().parent
BOOT = '4fde12b18f2f0ee7423d63a0165847a0075f2c67148100e77c09d01de08b33bd'
SYSTEM = '21adb51ecb11564fcd65f0f3700469d65b0877e55a7c8fec2d5eec6214571920'
VINTF = '/vendor/etc/vintf/manifest.xml'
RC = '/vendor/etc/init/android.hardware.gnss@1.0-service.rc'
FEATURE = '/vendor/etc/permissions/android.hardware.location.gps.xml'
ROUTE = '/vendor/bin/z1_audio_route.sh'
IMPL = '/vendor/lib/hw/android.hardware.gnss@1.0-impl.so'
RADIO = '/bin/z1_linux7_radio_init.sh'
BT_ACTION = (b'\non property:ro.persistent_properties.ready=true\n'
             b'    exec_background u:r:init:s0 root root -- /system/bin/sh '
             b'/system/bin/z1_linux7_radio_init.sh --bt-identity-only\n')
DEFAULT_CC = HERE.parents[1] / ('android-ndk-r26d/toolchains/llvm/prebuilt/linux-x86_64/bin/'
                                'armv7a-linux-androideabi28-clang')
ALLOW = re.compile('|'.join(re.escape(p) for p in (VINTF, RC, FEATURE, ROUTE, IMPL, RADIO)))


def remove_one(data, pattern):
    result, count = re.subn(pattern, b'', data, flags=re.M | re.S)
    system.require(count == 1, 'Expected exactly one matching configuration item')
    return result


def no_gnss_manifest(data):
    result = remove_one(data, rb'^    <hal format="hidl">\n        <name>android\.hardware\.gnss</name>\n(?:(?!</hal>).)*    </hal>\n')
    system.require(not any(e.findtext('name') == 'android.hardware.gnss'
                           for e in ET.fromstring(result).findall('hal')), 'GNSS remains declared')
    return result


def prepare(base, out, cc, bt_late):
    src = out / 'inputs'
    src.mkdir()

    def extract(path):
        return subprocess.run(['debugfs', '-R', f'cat {path}', str(base / 'system.img')],
                              check=True, capture_output=True).stdout

    for level in (1, 2, 3):
        entries = [e for e in ET.fromstring(extract(f'/etc/vintf/compatibility_matrix.{level}.xml')).findall('hal')
                   if e.findtext('name') == 'android.hardware.gnss']
        system.require(len(entries) == 1 and entries[0].get('optional') == 'true',
                       'Cannot defer mandatory GNSS')
    # Legacy libhidl can load a passthrough implementation even when no
    # transport was declared. Supply a factory that returns no interface.
    stub = src / 'gnss-unavailable.so'
    subprocess.run([cc, '-shared', '-nostdlib', '-x', 'c', '-', '-o', str(stub)],
                   input=b'void *HIDL_FETCH_IGnss(const char *name) { (void)name; return (void *)0; }\n',
                   check=True)
    check_arm(stub.read_bytes(), IMPL)
    replacements = []
    data = [
        (IMPL, 'd6c0bf6488912095716f4741472fdad20ffccbbe49b977abd4c3c0b892fbd8b9',
         lambda old: stub.read_bytes()),
        (VINTF, '554e08cdbca31343e79e4647449877849c818b0889e4955bafb11ba2cc855a08', no_gnss_manifest),
        (RC, '5d2f1d3c8fa0349b50207a84ad51dd8da1288f5fe8589d303206925568deca71',
         lambda old: b'# Optional hardware GNSS is deferred in this profile.\n'),
        (FEATURE, '24bcd790812b438782c59424ec0db74b4dff228d8a96919ce39cf428ffc9c903',
         lambda old: remove_one(old, rb'^    <feature name="android\.hardware\.location\.gps" />\n')),
        (ROUTE, '8af73480059626ab6f0e056e4ae7cc3d17f83e77cced0f6be10c3fb6cd7d4f58',
         lambda old: (HERE / 'r8_audio/z1_audio_route.sh').read_bytes()),
    ]
    if bt_late:
        data.append((RADIO, 'abb42410dd534fb264b31ea7146c2752424a6b2877bb17179214e5989f0d2e46',
                     lambda old: (HERE / 'z1_linux7_radio_init.sh').read_bytes()))
    for path, expected, transform in data:
        original = extract(path)
        system.require(sha(original) == expected, f'Unexpected base content: {path}')
        payload = transform(original)
        if path == FEATURE:
            features = {e.get('name') for e in ET.fromstring(payload).findall('feature')}
            system.require(features == {'android.hardware.location', 'android.hardware.location.network'},
                           'Network location declaration changed')
        if path == ROUTE:
            system.require(payload == original.replace(b'"MIC Route"', b'"MIC Route Switch"'),
                           'Unexpected audio route change')
        if path == RADIO:
            system.require(b'--bt-identity-only' in payload and b'ro.persistent_properties.ready' in payload,
                           'Missing Bluetooth late identity action')
            subprocess.run(['sh', '-n'], input=payload, check=True)
        source = src / Path(path).name
        source.write_bytes(payload)
        replacements.append(dict(path=path, source=str(source), original_sha256=expected,
                                 sha256=sha(payload), uid=0, gid=0, mode='0755' if path in (ROUTE, RADIO) else '0644',
                                 selinux='u:object_r:' + {ROUTE: 'vendor_file', IMPL: 'vendor_file',
                                                         RADIO: 'system_file'}.get(path, 'vendor_configs_file') + ':s0'))
    spec = out / 'system-inputs.json'
    spec.write_text(json.dumps(dict(version=1, base=dict(path=str(base / 'system.img'), sha256=SYSTEM),
                                   replacements=replacements), indent=2) + '\n')
    return spec


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('base', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--cc', default=os.environ.get('Z1_ANDROID_CC', str(DEFAULT_CC)))
    parser.add_argument('--bt-late', action='store_true', help='Add BT identity prep after persistent properties load')
    args = parser.parse_args()
    base, out = args.base.resolve(), args.output.resolve()
    out.mkdir()
    spec = prepare(base, out, args.cc, args.bt_late)
    raw = (base / 'boot.img').read_bytes()
    system.require(sha(raw) == BOOT, 'Unexpected R7 boot identity')
    _, _, kernel, dtb, rootfs = split_boot(raw)
    records = contents(*cpio(rootfs))
    old = records['init.z1.network.rc']
    new = remove_one(old, rb'^on property:sys\.z1\.radio\.modules_ready=1\n    exec_background [^\n]* /z1_gps_prep\.sh\n')
    system.require(b'/z1_gps_prep.sh' not in new, 'Active GPS prep remains')
    if args.bt_late:
        system.require(b'--bt-identity-only' not in new, 'Duplicate Bluetooth identity action')
        new += BT_ACTION

    def boot_source(name, payload):
        p = out / 'inputs' / name
        p.write_bytes(payload)
        return dict(path=str(p), sha256=sha(payload))

    boot_spec = out / 'boot-inputs.json'
    boot_spec.write_text(json.dumps(dict(version=1, base=dict(path=str(base / 'boot.img'), sha256=BOOT),
                                       zimage=boot_source('zImage', kernel), dtb=boot_source('base.dtb', dtb),
                                       replacements=[dict(name='init.z1.network.rc', original_sha256=sha(old),
                                                          source=boot_source('init.z1.network.rc', new))]), indent=2) + '\n')
    boot.ALLOWED.add('init.z1.network.rc')
    boot.build(boot_spec, out / 'boot-package')
    report = system.execute(spec, out / 'system.img', build=True, allowed=ALLOW)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()

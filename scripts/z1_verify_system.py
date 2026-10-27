#!/usr/bin/env python3
"""Static audit of the Z1 A9 system image: for every HAL declared in the device
manifest, check that a service binary, an init .rc and an impl library exist.
Also reports the subsystem pieces each feature needs."""
import re
import subprocess
import sys

IMG = sys.argv[1] if len(sys.argv) > 1 else '/home/lxj/claude/6572/out/z1_r3f_system/system.img'


def ls(path):
    out = subprocess.run(['debugfs', '-R', f'ls {path}', IMG],
                         capture_output=True, text=True).stdout
    return {t for t in out.replace('\n', ' ').split(' ')
            if t and t not in ('.', '..') and not t.isdigit() and not t.startswith('(')}


def cat(path):
    return subprocess.run(['debugfs', '-R', f'cat {path}', IMG],
                          capture_output=True, text=True).stdout


def main():
    manifest = cat('/vendor/etc/vintf/manifest.xml')
    if not manifest.strip():
        sys.exit('no device manifest')

    # collect declared fqnames: package, version, interface
    hals = []
    for block in re.findall(r'<hal.*?</hal>', manifest, re.S):
        name = re.search(r'<name>(.*?)</name>', block)
        ver = re.search(r'<version>(.*?)</version>', block)
        ifaces = re.findall(r'<interface>\s*<name>(.*?)</name>\s*<instance>(.*?)</instance>', block, re.S)
        transport = re.search(r'<transport[^>]*>(.*?)</transport>', block)
        if name and ver:
            hals.append(dict(name=name.group(1), version=ver.group(1),
                             ifaces=[i[0] for i in ifaces],
                             transport=(transport.group(1) if transport else '?')))

    vendor_hw = ls('/vendor/lib/hw')
    sys_hw = ls('/lib/hw')
    vendor_bin_hw = ls('/vendor/bin/hw')
    vendor_init = ls('/vendor/etc/init')
    sys_init = ls('/etc/init')

    print(f"{'HAL':42} {'service':8} {'rc':4} {'impl':6} transport")
    print('-' * 84)
    missing = []
    for h in hals:
        pkg, ver = h['name'], h['version']
        base = f"{pkg}@{ver}"
        svc = any(f.startswith(base) and f.endswith('service') for f in vendor_bin_hw)
        rc = any(f.startswith(base) and f.endswith('.rc') for f in vendor_init | sys_init)
        impl = any(f.startswith(base) and '-impl' in f for f in vendor_hw | sys_hw)
        print(f"{base:42} {'yes' if svc else 'NO':8} {'yes' if rc else 'NO':4} "
              f"{'yes' if impl else 'NO':6} {h['transport']}")
        if not (svc or impl):
            missing.append(base)
    print()
    if missing:
        print('HALs with neither a service binary nor an impl:', ', '.join(missing))
    else:
        print('every declared HAL has a service binary or an impl library')

    # subsystem presence checks
    checks = {
        'wifi: wpa_supplicant': '/bin/wpa_supplicant',
        'wifi: wificond': '/bin/wificond',
        'wifi: modules': '/lib/modules/z1-radio/wlan_gen2.ko',
        'bt: hw module': '/vendor/lib/hw/bluetooth.default.so',
        'bt: vendor lib': '/vendor/lib/libbt-vendor.so',
        'bt: mtk driver lib': '/vendor/lib/libbluetoothdrv.so',
        'bt: module': '/lib/modules/z1-radio/mtk_stp_bt_soc.ko',
        'audio: modules': '/lib/modules/mt6572-mt6323.ko',
        'audio: hal': '/vendor/lib/hw/audio.primary.mt6572.so',
        'gpu: swiftshader': '/vendor/lib/egl/libEGL_swiftshader.so',
        'gpu: mesa dri': '/vendor/lib/dri/lima_dri.so',
        'gpu: mesa egl': '/vendor/lib/egl/libEGL_mesa.so',
        'keys: keylayout': '/usr/keylayout/mt6779_keypad.kl',
        'keys: touch kl': '/usr/keylayout/z1_cst836u.kl',
        'gps: gps hal': '/vendor/lib/hw/gps.default.so',
        'gps: mnld': '/bin/mnld',
        'sensors: hal': '/vendor/lib/hw/sensors.mt6572.so',
    }
    print()
    for label, path in checks.items():
        exists = path.rsplit('/', 1)[1] in ls(path.rsplit('/', 1)[0])
        print(f"{'OK ' if exists else 'MISS'} {label:24} {path}")


if __name__ == '__main__':
    main()

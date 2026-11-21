#!/usr/bin/env python3
"""Build the Z1 Bluetooth and audio repair on the archived R7 pair."""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import z1_build_adaptation_boot as boot_builder
import z1_prepare_recovery_boot as boot
from release import z1_build_driver_candidate as system

BASE_BOOT = '0d19730f915bece3e2dd1110baeadcfd8a03ca58f7e371dfd3cb2492c6903d18'
BASE_SYSTEM = 'a3fc264d50e9b8e9a8024622637517b70d031cb1d5fe985e3da50b04ce982565'
SCATTER = 'MT6572_Z1_R7_STABILITY_scatter.txt'
SCATTER_SHA = 'a1f34ae8d68924edb7275c80c0e0370016268d70928f099d6748c5b832fa713a'


def build(base, output, bt, audio, settings):
    boot_path = base / 'boot.img'
    system_path = base / 'system.img'
    boot.require(system.sha(boot_path) == BASE_BOOT and system.sha(system_path) == BASE_SYSTEM,
                 'expected the archived R7 Bluetooth compatibility pair')
    scatter = (base / SCATTER).read_bytes()
    boot.require(boot.sha(scatter) == SCATTER_SHA, 'unexpected scatter digest')
    enabled = re.findall(rb'partition_name: (\S+)\s+file_name: (\S+)\s+is_download: true', scatter)
    boot.require(enabled == [(b'bootimg', b'boot.img'), (b'android', b'system.img')],
                 'unexpected scatter download set')
    boot.require(not output.exists() and not output.is_symlink(), 'output already exists')

    with tempfile.TemporaryDirectory(prefix='.z1-factory-repair-', dir=output.parent) as td:
        work = Path(td)
        package = work / 'package'
        package.mkdir()
        _, _, zimage, dtb, rootfs = boot.split_boot(boot_path.read_bytes())
        old = boot.contents(*boot.cpio(rootfs))
        inputs = {'version': 1, 'base': {'path': str(boot_path), 'sha256': BASE_BOOT}}
        for name, data in {'zimage': zimage, 'dtb': dtb}.items():
            path = work / name
            path.write_bytes(data)
            inputs[name] = {'path': str(path), 'sha256': boot.sha(data)}
        inputs['replacements'] = [
            {'name': name, 'source': {'path': str(path), 'sha256': system.sha(path)},
             'original_sha256': boot.sha(old[name])}
            for name, path in [('z1_libbt-vendor.so', bt), ('z1_audio.primary.mt6572.so', audio)]]
        boot_spec = work / 'boot.json'
        boot_spec.write_text(json.dumps(inputs))
        boot_report = boot_builder.build(boot_spec, work / 'boot')
        shutil.copyfile(work / 'boot/boot.img', package / 'boot.img')

        radio = SCRIPTS / 'z1_linux7_radio_init.sh'
        boot.require(b'/APRDCL/FILE_VER' in radio.read_bytes(), 'radio version check missing')
        replacements = {
            '/bin/z1_linux7_radio_init.sh': (radio, '0755', 'system_file'),
            '/bin/z1diag/z1_diag_audio.sh': (SCRIPTS / 'diag/z1_diag_audio.sh', '0755', 'system_file'),
            '/vendor/lib/libbt-vendor.so': (bt, '0644', 'vendor_file'),
            '/vendor/lib/hw/audio.primary.mt6572.so': (audio, '0644', 'vendor_file'),
            '/vendor/lib/hw/audio.primary.default.so': (audio, '0644', 'vendor_file'),
            '/vendor/lib/libaudio.primary.default.so': (audio, '0644', 'vendor_file'),
        }
        if settings:
            replacements['/priv-app/Settings/Settings.apk'] = (settings, '0644', 'system_file')
        entries = []
        for path, (source, mode, label) in replacements.items():
            original = subprocess.run(['debugfs', '-R', f'cat {path}', str(system_path)],
                                      check=True, capture_output=True).stdout
            boot.require(bool(original), 'missing image member: ' + path)
            if path.endswith('.sh'):
                subprocess.run(['sh', '-n', str(source)], check=True)
            entries.append({'path': path, 'source': str(source), 'mode': mode, 'uid': 0, 'gid': 0,
                            'selinux': f'u:object_r:{label}:s0', 'sha256': system.sha(source),
                            'original_sha256': boot.sha(original)})
        spec = work / 'system.json'
        spec.write_text(json.dumps({'version': 1, 'base': {'path': str(system_path), 'sha256': BASE_SYSTEM},
                                    'replacements': entries}))
        system.ENV = dict(system.ENV, E2FSPROGS_FAKE_TIME='1795273200')
        system.execute(spec, package / 'system.img', True,
                       allowed=re.compile('|'.join(map(re.escape, replacements))))
        (package / SCATTER).write_bytes(scatter)
        (package / 'boot-manifest.json').write_text(json.dumps(boot_report, indent=2) + '\n')
        (package / 'PACKAGE_NOTE.md').write_text(
            '# Z1 R7 factory Bluetooth and audio repair\n\n'
            'Host verified. Bluetooth discovery/pairing and audible speaker output require board testing.\n\n'
            f'Settings APK replaced: {bool(settings)}. Boot SHA256: `{boot_report["boot_sha256"]}`.\n\n'
            'Use Download Only with bootimg and android. Preserve userdata.\n')
        (package / 'SHA256SUMS').write_text(''.join(
            f'{system.sha(file)}  {file.name}\n' for file in sorted(package.iterdir()) if file.is_file()))
        package.rename(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('base', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--bt-vendor', type=Path, required=True)
    parser.add_argument('--audio-hal', type=Path, required=True)
    parser.add_argument('--settings', type=Path)
    args = parser.parse_args()
    build(args.base.resolve(strict=True), args.output.absolute(), args.bt_vendor.resolve(strict=True),
          args.audio_hal.resolve(strict=True), args.settings.resolve(strict=True) if args.settings else None)


if __name__ == '__main__':
    main()

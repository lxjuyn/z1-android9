#!/usr/bin/env python3
"""Opt-in read-only Pie init/service/classpath checks on an actual raw system image.

Requires the MTK-header ramdisk and its matching system staging tree. This does
not execute Android or claim device boot. Every subprocess is bounded by 180s.
"""
import argparse
import json
from pathlib import Path, PurePosixPath
import re
import posixpath
import shlex
import stat
import sys
import unittest
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile

from z1_verify_linux7_candidate import (ImageFiles, arm_elf, check_elf_closure,
                                       ramdisk_files, require, run, sha)


def services(text, source):
    """Parse only service sections; fail closed for malformed service headers."""
    result = []
    current = None
    logical = []
    pending = ''
    first = 0
    for number, physical in enumerate(text.splitlines(), 1):
        if not pending:
            first = number
        pending += physical if not pending else physical.lstrip()
        if pending.endswith('\\'):
            pending = pending[:-1] + ' '
            continue
        logical.append((first, pending))
        pending = ''
    require(not pending, f'{source}: unterminated line continuation')
    for number, line in logical:
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        if not line[0].isspace():
            current = None
            if not line.startswith('service '):
                require(line != 'service', f'{source}:{number}: malformed service')
                continue
            tokens = shlex.split(line, comments=True)
            if tokens and tokens[0] == 'service':
                require(len(tokens) >= 3, f'{source}:{number}: malformed service')
                require(re.fullmatch(r'[A-Za-z0-9_.@-]+', tokens[1]), 'unsafe service name')
                require(tokens[2].startswith('/') and '${' not in tokens[2],
                        f'{source}:{number}: nonliteral executable')
                current = {'name': tokens[1], 'executable': tokens[2], 'options': [],
                           'source': source, 'line': number}
                result.append(current)
        elif current is not None:
            current['options'].append(shlex.split(line, comments=True))
    return result


def optional_recovery(service):
    return (service['name'] == 'flash_recovery' and
            service['executable'] == '/system/bin/install-recovery.sh' and
            ['disabled'] in service['options'] and ['oneshot'] in service['options'])


def system_relative(absolute):
    require(absolute.startswith(('/system/', '/vendor/')), f'not system path: {absolute}')
    return absolute.removeprefix('/system/') if absolute.startswith('/system/') else 'vendor/' + absolute[8:]


def resolve_image_file(image, relative):
    """Verify every symlink in the executable path before following its target."""
    for _ in range(16):
        source = image.tree / relative
        if not source.is_symlink():
            return relative, image.get(relative)
        target = source.readlink().as_posix()
        output = run(['debugfs', '-R', f'stat /{relative}', str(image.image)]).stdout
        require('Type: symlink' in output and f'Fast link dest: "{target}"' in output,
                f'image executable symlink differs: {relative}')
        if target.startswith('/'):
            relative = system_relative(target)
        else:
            # Normalize only after bounding the traversal to the system tree.
            resolved = (source.parent / target).resolve()
            require(resolved.is_relative_to(image.tree), 'executable symlink escapes system')
            relative = resolved.relative_to(image.tree).as_posix()
    raise ValueError('executable symlink loop')


def verify(args):
    files = ramdisk_files(args.ramdisk.read_bytes())
    report = {'status': 'FAIL', 'device_runtime_verified': False,
              'services': [], 'optional_missing': [], 'ramdisk_executables': []}
    with tempfile.TemporaryDirectory(prefix='z1-init-check-') as directory:
        temp = Path(directory)
        image = ImageFiles(args.system, args.system_tree, temp / 'system')
        definitions = []
        defaults = files['default.prop'][1].decode()
        require('ro.zygote=zygote32' in defaults.splitlines(), 'unsupported or missing ro.zygote')
        init = files['init.rc'][1].decode()
        board_import = '/init.z1.rc' if args.stock else '/init.${ro.hardware}.rc'
        expected_imports = ('/init.environ.rc', '/init.usb.rc', board_import,
                            '/init.usb.configfs.rc', '/init.${ro.zygote}.rc')
        for imported in expected_imports:
            require('import ' + imported in init.splitlines(), f'missing init import {imported}')
            expanded = imported.replace('${ro.hardware}', 'z1').replace('${ro.zygote}', 'zygote32')
            require(expanded.lstrip('/') in files, f'missing imported RC {expanded}')
        if args.stock:
            require('z1_stock_kernel' in files, 'stock marker missing')
            require('import /init.z1.network.rc' not in files['init.z1.rc'][1].decode(),
                    'stock must not load Linux7 modules')
            require(b'--graphics-mode' in files['init.z1.rc'][1], 'missing stock graphics takeover')
            require(b'start hwservicemanager' not in files['init.rc'][1], 'stock starts hwservicemanager')
            require(b'start vndservicemanager' not in files['init.rc'][1], 'stock starts vndservicemanager')
            require(b'--stock-partitions' in files['init.z1.rc'][1], 'stock has no live partition resolver')
            require(b'/dev/block/z1-stock-' in files['fstab.z1'][1], 'stock fstab has no resolved aliases')
            props = image.get('build.prop').read_text().splitlines()
            for line in ('ro.z1.stock_kernel=true', 'ro.logd.kernel=false',
                         'ro.hardware.gralloc=z1', 'ro.hardware.hwcomposer=z1'):
                require(line in props, 'missing stock property: ' + line)
        else:
            require('import /init.z1.network.rc' in files['init.z1.rc'][1].decode().splitlines(),
                    'board network RC not imported')
        for name, (fields, data) in sorted(files.items()):
            if '/' not in name and name.endswith('.rc'):
                require(stat.S_ISREG(fields[1]), f'ramdisk RC not regular: {name}')
                definitions += services(data.decode(), '/' + name)
        for subdirectory in ('etc/init', 'vendor/etc/init'):
            for rc in sorted((image.tree / subdirectory).glob('*.rc')):
                relative = rc.relative_to(image.tree).as_posix()
                definitions += services(image.get(relative).read_text(), '/system/' + relative)
        names = [s['name'] for s in definitions]
        require(len(names) == len(set(names)), 'duplicate service definitions')
        roots = set()
        if args.stock:
            for service in definitions:
                exe = service['executable']
                unsupported = (service['name'] in ('hwservicemanager', 'vndservicemanager', 'hidl_memory', 'media.codec')
                               or exe.startswith(('/vendor/bin/hw/', '/system/bin/hw/')))
                require(not unsupported or ['disabled'] in service['options'],
                        'stock remote HIDL server is enabled: ' + service['name'])
            factories = {
                'android.hardware.graphics.composer@2.1-impl.so': 'IComposer',
                'android.hardware.graphics.allocator@2.0-impl.so': 'IAllocator',
                'android.hardware.graphics.mapper@2.0-impl.so': 'IMapper',
                'android.hardware.audio@2.0-impl.so': 'IDevicesFactory',
                'android.hardware.audio.effect@2.0-impl.so': 'IEffectsFactory',
                'android.hardware.keymaster@3.0-impl.so': 'IKeymasterDevice',
                'android.hardware.configstore@1.1-impl.z1stock.so': 'ISurfaceFlingerConfigs',
            }
            report['stock_factories'] = []
            for filename, interface in factories.items():
                relative = 'vendor/lib/hw/' + filename
                path = image.get(relative)
                symbols = run(['readelf', '--dyn-syms', '--wide', str(path)]).stdout
                require('HIDL_FETCH_' + interface in symbols, 'missing stock factory: ' + relative)
                roots.add(relative)
                report['stock_factories'].append(relative)
        for service in definitions:
            exe = service['executable']
            if exe.startswith(('/system/', '/vendor/')):
                relative = system_relative(exe)
                if not (image.tree / relative).exists() and optional_recovery(service):
                    image.require_absent(relative)
                    report['optional_missing'].append(service)
                    continue
                relative, path = resolve_image_file(image, relative)
                inode = run(['debugfs', '-R', f'stat /{relative}', str(image.image)]).stdout
                mode = re.search(r'Mode:\s*(0?[0-7]+)', inode)
                require(mode and int(mode.group(1), 8) & 0o111,
                        f'non-executable image service: {exe}')
                if path.read_bytes()[:4] == b'\x7fELF':
                    roots.add(relative)
                else:
                    require(path.read_bytes().startswith(b'#!/system/bin/sh'),
                            f'unsupported service script interpreter: {exe}')
                    interpreter_relative, _ = resolve_image_file(image, 'bin/sh')
                    roots.add(interpreter_relative)
                report['services'].append(service)
            else:
                name = exe.lstrip('/')
                for _ in range(16):
                    require(name in files, f'missing ramdisk executable: {exe}')
                    fields, data = files[name]
                    if not stat.S_ISLNK(fields[1]):
                        break
                    target = data.decode()
                    joined = target.lstrip('/') if target.startswith('/') else str(PurePosixPath(name).parent / target)
                    name = posixpath.normpath(joined)
                    require(name != '..' and not name.startswith('../'),
                            'ramdisk symlink escapes root')
                else:
                    raise ValueError('ramdisk executable symlink loop')
                require(stat.S_ISREG(fields[1]) and fields[1] & 0o111,
                        f'non-executable ramdisk service: {exe}')
                path = temp / ('ramdisk-' + name.replace('/', '_'))
                path.write_bytes(data)
                arm_elf(path)
                header = run(['readelf', '-l', str(path)]).stdout
                for interpreter in re.findall(r'Requesting program interpreter: ([^]]+)', header):
                    require(interpreter == '/system/bin/linker', 'unexpected ramdisk ELF interpreter')
                    roots.add('bin/linker')
                report['ramdisk_executables'].append({'path': exe, 'target': name})
                report['services'].append(service)
        report['elf_closure'] = check_elf_closure(image, sorted(roots))
        environ = files['init.environ.rc'][1].decode()
        report['classpaths'] = {}
        for kind in ('BOOTCLASSPATH', 'SYSTEMSERVERCLASSPATH'):
            matches = re.findall(r'^\s*export ' + kind + r' (\S+)\s*$', environ, re.M)
            require(len(matches) == 1, f'missing/duplicate {kind}')
            paths = matches[0].split(':')
            require(len(paths) == len(set(paths)), f'duplicate {kind} entry')
            for absolute in paths:
                jar = image.get(system_relative(absolute))
                with zipfile.ZipFile(jar) as archive:
                    require(archive.testzip() is None, f'corrupt classpath archive: {absolute}')
                    require(any(re.fullmatch(r'classes[0-9]*\.dex', n) for n in archive.namelist()),
                            f'classpath JAR lacks dex: {absolute}')
            report['classpaths'][kind] = paths
        manifest = ET.parse(image.get('vendor/etc/vintf/manifest.xml')).getroot()
        configstores = [h for h in manifest.findall('hal') if h.findtext('name') == 'android.hardware.configstore']
        require(len(configstores) == 1 and configstores[0].findtext('version') == '1.1' and
                configstores[0].findtext('transport') == ('passthrough' if args.stock else 'hwbinder'),
                'configstore image manifest transport/version mismatch')
        require(any(s['executable'] == '/vendor/bin/hw/android.hardware.configstore@1.1-service'
                    for s in report['services']), 'missing configstore 1.1 executable service')
        report['service_count'] = len(report['services'])
        report['elf_root_count'] = len(roots)
        report['elf_roots'] = sorted(roots)
        report['verified_image_files'] = image.checked
        report['inputs'] = {k: {'path': str(getattr(args, k)), 'sha256': sha(getattr(args, k))}
                            for k in ('system', 'ramdisk')}
        report['status'] = 'STATIC_INIT_CLOSURE_PASS'
    return report


def self_test():
    class ParserChecks(unittest.TestCase):
        def test_continuation(self):
            text = 'service foo /system/bin/foo ' + chr(92) + '\n    --flag\n    class core\n'
            parsed = services(text, 'test')
            self.assertEqual(len(parsed), 1)
            self.assertEqual(parsed[0]['executable'], '/system/bin/foo')
            self.assertEqual(parsed[0]['options'], [['class', 'core']])
        def test_malformed(self):
            for text in ('service', 'service foo', 'service foo ${foo}/bin/x'):
                with self.subTest(text=text), self.assertRaises(ValueError):
                    services(text, 'test')
        def test_unfinished_continuation(self):
            with self.assertRaises(ValueError):
                services('service foo /x ' + chr(92), 'test')
        def test_optional_exact(self):
            parsed = services('service flash_recovery /system/bin/install-recovery.sh\n    disabled\n    oneshot\n', 'test')[0]
            self.assertTrue(optional_recovery(parsed))
            parsed['name'] = 'other'
            self.assertFalse(optional_recovery(parsed))
        def test_optional_requires_disabled(self):
            parsed = services('service flash_recovery /system/bin/install-recovery.sh\n    oneshot', 'test')[0]
            self.assertFalse(optional_recovery(parsed))
        def test_vendor_map(self):
            self.assertEqual(system_relative('/vendor/bin/x'), 'vendor/bin/x')
        def test_reject_other_root(self):
            with self.assertRaises(ValueError):
                system_relative('/tmp/x')
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ParserChecks))
    return 0 if result.wasSuccessful() else 1


def main():
    if sys.argv[1:] == ['--self-test']:
        return self_test()
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('system', 'system-tree', 'ramdisk', 'report'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--stock', action='store_true', help='verify isolated stock-kernel init profile')
    args = parser.parse_args()
    try:
        report = verify(args)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, ET.ParseError,
            zipfile.BadZipFile) as exc:
        report = {'status': 'FAIL', 'device_runtime_verified': False, 'error': str(exc)}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k not in
                      ('services', 'elf_closure', 'verified_image_files', 'classpaths')}, indent=2))
    return 0 if report['status'] == 'STATIC_INIT_CLOSURE_PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())

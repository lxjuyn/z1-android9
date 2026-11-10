#!/usr/bin/env python3
"""Host-only GPS shell regressions. Run: python3 test_gps_scripts.py -v

No module, device, mount, init service or board is touched. In temporary copies,
absolute Android paths are rewritten ONCE and sensitive commands are shell
functions (not PATH mocks: BusyBox ash may prefer built-in applets). Character
devices and init/module responses are simulated, not hardware validation.
"""
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
PREP = HERE.parents[1] / 'ramdisk_v9/z1_gps_prep.sh'
ATTRS = ('status', 'state', 'pwrctl', 'suspend', 'pwrsave')
COMMANDS = ('insmod', 'mknod', 'test', 'stat', 'chown', 'chmod', 'getprop',
            'start', 'stop', 'setprop', 'mount', 'cp', 'mv', 'sleep', 'id', 'sh')
SHELLS = [[shutil.which('dash') or '/bin/sh']]
if shutil.which('busybox'):
    SHELLS.append([shutil.which('busybox'), 'ash'])
if shutil.which('bash'):
    SHELLS.append([shutil.which('bash')])


def mock(command, args):
    root = Path(os.environ['GPS_TEST_ROOT']).resolve()
    state_file = root / 'state.json'
    state = json.loads(state_file.read_text())
    with (root / 'calls.jsonl').open('a') as f:
        f.write(json.dumps([command, args]) + '\n')

    def path(value):
        p = Path(value)
        # Never follow a fixture symlink outside the temporary root.
        p.resolve().relative_to(root)
        return p

    def save():
        state_file.write_text(json.dumps(state))

    if command in state.get('fail', []):
        print('injected ' + command + ' failure', file=sys.stderr)
        return 1
    if command == 'insmod':
        ko = path(args[0])
        assert ko.is_file()
        if ko.name == state.get('fail_module'):
            return 1
        if ko.name == 'z1_gpsdrv.ko':
            d = root / 'sys/class/gpsdrv/gps'
            d.mkdir(parents=True, exist_ok=True)
            for a in ATTRS:
                if a != state.get('omit_attr'):
                    (d / a).touch()
        elif ko.name == 'mtk_stp_gps_soc.ko':
            if not state.get('no_registration'):
                major = state.get('registered_major', 191)
                (root / 'proc/devices').write_text(
                    f'Character devices:\n{major} mtk_stp_GPS_chrdev\n\nBlock devices:\n')
        else:
            raise AssertionError('unexpected module')
    elif command == 'mknod':
        assert args[1] == 'c' and len(args) == 4
        p = path(args[0])
        p.touch()
        state['node_rdev'] = f'{int(args[2]):x}:{int(args[3]):x}'
        save()
    elif command == 'test':
        assert len(args) == 2 and args[0] == '-c'
        return 0 if path(args[1]).is_file() and state.get('node_rdev') else 1
    elif command == 'stat':
        assert args[:2] == ['-c', '%t:%T']
        path(args[2])
        print(state.get('node_rdev', '0:0'))
    elif command in ('chmod', 'chown'):
        path(args[-1])
    elif command == 'getprop':
        print(state.get('props', {}).get(args[0], ''))
    elif command == 'start':
        assert args == ['mnld']
        if not state.get('never_running'):
            state['props']['init.svc.mnld'] = 'running'
        save()
    elif command in ('stop', 'setprop'):
        raise AssertionError('must not fake service state or stop another service')
    elif command == 'mv':
        source, destination = map(path, args)
        assert not destination.exists()
        source.rename(destination)
    elif command == 'cp':
        source, destination = map(path, args)
        shutil.copy2(source, destination)
    elif command == 'id':
        assert args == ['-u']
        print(state.get('uid', 0))
    elif command == 'mount':
        assert args == ['-o', 'remount,rw', '/']
    elif command == 'sleep':
        assert args == ['1']
    elif command == 'sh':
        target = path(args[0])
        assert '# GPS_HOST_TEST_COPY' in target.read_text()
        return subprocess.run(json.loads(os.environ['GPS_TEST_SHELL']) +
                              [str(target)] + args[1:], env=os.environ).returncode
    else:
        raise AssertionError(command)
    return 0


class Fixture:
    def __init__(self, shell, state=None):
        self.tmp = tempfile.TemporaryDirectory(prefix='z1-gps-regression-')
        self.root = Path(self.tmp.name)
        self.shell = shell
        for d in ('data/misc', 'dev', 'proc', 'vendor/bin', 'system/xbin',
                  'system/lib/modules/z1-radio', 'system/etc/permissions', 'package/z1_gpsdrv'):
            (self.root / d).mkdir(parents=True)
        initial = {'props': {'init.svc.mnld': 'stopped'}}
        initial.update(state or {})
        self.set_state(initial)
        (self.root / 'proc/devices').write_text('Character devices:\n\nBlock devices:\n')
        for f in ('vendor/bin/mnld', 'system/xbin/libmnlp_mt6572'):
            p = self.root / f
            p.touch()
            p.chmod(0o755)
        for f in ('z1_gpsdrv.ko', 'mtk_stp_gps_soc.ko'):
            (self.root / 'system/lib/modules/z1-radio' / f).touch()
        (self.root / 'package/z1_gpsdrv/z1_gpsdrv.ko').write_bytes(b'fixture module, never loaded')
        (self.root / 'package/android.hardware.location.gps.xml').write_text('<permissions/>')
        sources = {'prep.sh': PREP, 'z1_gps_prep.sh.patched': HERE / 'z1_gps_prep.sh.patched',
                   'install_gps.sh': HERE / 'install_gps.sh',
                   'z1_gpsdrv_load.sh': HERE / 'z1_gpsdrv_load.sh'}
        # Single regex substitution prevents rewriting paths inside replacements.
        pattern = re.compile(r'/(?:data|system|sys|proc|vendor)(?=/)|/dev/(?:kmsg|stpgps)')
        wrappers = '\n'.join(
            f'{c}() {{ {shlex.quote(sys.executable)} {shlex.quote(str(Path(__file__).resolve()))} '
            f'--mock {c} "$@"; }}' for c in COMMANDS)
        for name, source in sources.items():
            text = pattern.sub(lambda m: str(self.root) + m.group(), source.read_text())
            text = '# GPS_HOST_TEST_COPY\n' + wrappers + '\n' + text
            (self.root / 'package' / name).write_text(text)
        self.env = {'PATH': '/usr/bin:/bin', 'GPS_TEST_ROOT': str(self.root),
                    'GPS_TEST_SHELL': json.dumps(shell), 'PYTHONDONTWRITEBYTECODE': '1',
                    'LC_ALL': 'C'}

    def set_state(self, state):
        (self.root / 'state.json').write_text(json.dumps(state))

    def patch_state(self, **kwargs):
        state = json.loads((self.root / 'state.json').read_text())
        state.update(kwargs)
        self.set_state(state)

    def run(self, name='prep.sh', *args):
        result = subprocess.run(self.shell + [str(self.root / 'package' / name), *args],
                                env=self.env, capture_output=True, text=True, timeout=15)
        if 'Traceback' in result.stderr:
            raise AssertionError('mock failed: ' + result.stderr)
        return result

    def calls(self, name=None):
        p = self.root / 'calls.jsonl'
        calls = [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []
        return [args for c, args in calls if c == name] if name else calls

    def attrs(self, omit=None):
        d = self.root / 'sys/class/gpsdrv/gps'
        d.mkdir(parents=True)
        for a in ATTRS:
            if a != omit:
                (d / a).touch()

    def close(self):
        self.tmp.cleanup()


class GPSRegression(unittest.TestCase):
    def each(self, scenarios):
        for shell in SHELLS:
            for name, body in scenarios:
                with self.subTest(shell=' '.join(shell), case=name):
                    f = Fixture(shell)
                    try:
                        body(f)
                    finally:
                        f.close()

    def success(self, f, name='prep.sh', *args):
        result = f.run(name, *args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(f.calls('setprop'))

    def failure(self, f, name='prep.sh', *args):
        result = f.run(name, *args)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(f.calls('setprop'))

    def test_syntax_and_entrypoints(self):
        self.assertEqual(PREP.read_bytes(), (HERE / 'z1_gps_prep.sh.patched').read_bytes())
        for shell in SHELLS:
            for source in (PREP, HERE / 'z1_gps_prep.sh.patched',
                           HERE / 'z1_gpsdrv_load.sh', HERE / 'install_gps.sh'):
                self.assertEqual(subprocess.run(shell + ['-n', str(source)],
                                               capture_output=True).returncode, 0)

    def test_missing_empty_and_exact_valid_configs(self):
        def case(payload):
            def check(f):
                p = f.root / 'data/misc/GPS_CHIP.cfg'
                if payload is not None:
                    p.write_bytes(payload)
                self.success(f)
                self.assertEqual(f.calls('start'), [['mnld']])
                self.assertEqual(p.read_bytes() if p.exists() else None, payload)
            return check
        self.each([(repr(p), case(p)) for p in
                   (None, b'', b'0x6572', b'0x6572\x00', b'0x6572\x00XYZ')])

    def test_known_bad_seed_archived_once(self):
        def check(f):
            p = f.root / 'data/misc/GPS_CHIP.cfg'
            seed = b'MTK_GPS_CHIP=MT6620\n'
            p.write_bytes(seed)
            self.success(f)
            self.assertFalse(p.exists())
            archived = list(p.parent.glob('GPS_CHIP.cfg.bad-*'))
            self.assertEqual(len(archived), 1)
            self.assertEqual(archived[0].read_bytes(), seed)
            self.success(f)
            self.assertEqual(len(f.calls('mv')), 1)
            self.assertEqual(len(f.calls('start')), 1)
        self.each([('migration and second run', check)])

    def test_invalid_configs_preserved_and_rejected(self):
        def case(payload):
            def check(f):
                p = f.root / 'data/misc/GPS_CHIP.cfg'
                p.write_bytes(payload)
                self.failure(f)
                self.assertEqual(p.read_bytes(), payload)
                self.assertFalse(f.calls('start'))
                self.assertFalse(f.calls('insmod'))
            return check
        self.each([(repr(p[:25]), case(p)) for p in
                   (b'0x6572\n', b'0x6572junk', b'0x9999', b'x' * 100,
                    b'MTK_GPS_CHIP=MT6620', b'MTK_GPS_CHIP=MT6620\nextra')])

    def test_config_archive_failure(self):
        def check(f):
            p = f.root / 'data/misc/GPS_CHIP.cfg'
            p.write_bytes(b'MTK_GPS_CHIP=MT6620\n')
            f.patch_state(fail=['mv'])
            self.failure(f)
            self.assertEqual(p.read_bytes(), b'MTK_GPS_CHIP=MT6620\n')
            self.assertFalse(f.calls('start'))
        self.each([('mv failure', check)])

    def test_service_and_executable_prerequisites(self):
        def case(change):
            def check(f):
                change(f)
                self.failure(f)
                self.assertFalse(f.calls('start'))
                self.assertFalse(f.calls('insmod'))
            return check
        self.each([
            ('missing service', case(lambda f: f.patch_state(props={}))),
            ('only z1_mnld', case(lambda f: f.patch_state(props={'init.svc.z1_mnld': 'running'}))),
            ('getprop fails', case(lambda f: f.patch_state(fail=['getprop']))),
            ('missing mnld', case(lambda f: (f.root / 'vendor/bin/mnld').unlink())),
            ('helper mismatch', case(lambda f: (f.root / 'data/misc/GPS_CHIP.cfg').write_bytes(b'0x6620'))),
        ])

    def test_module_failures_never_create_node(self):
        def case(change):
            def check(f):
                change(f)
                self.failure(f)
                self.assertFalse(f.calls('mknod'))
                self.assertFalse(f.calls('start'))
                calls = f.calls('insmod')
                self.assertEqual(len(calls), len({a[0] for a in calls}))
            return check
        self.each([
            ('missing gpsdrv', case(lambda f: (f.root / 'system/lib/modules/z1-radio/z1_gpsdrv.ko').unlink())),
            ('gpsdrv insmod fails', case(lambda f: f.patch_state(fail_module='z1_gpsdrv.ko'))),
            ('missing STP', case(lambda f: (f.root / 'system/lib/modules/z1-radio/mtk_stp_gps_soc.ko').unlink())),
            ('STP insmod fails', case(lambda f: f.patch_state(fail_module='mtk_stp_gps_soc.ko'))),
            ('insmod no registration', case(lambda f: f.patch_state(no_registration=True))),
            ('new shim incomplete', case(lambda f: f.patch_state(omit_attr='status'))),
            ('preloaded shim incomplete', case(lambda f: f.attrs(omit='pwrsave'))),
        ])

    def test_registered_major_and_existing_nodes(self):
        def success_major(f):
            f.patch_state(registered_major=192)
            self.success(f)
            self.assertEqual(f.calls('mknod')[0][1:], ['c', '192', '0'])
        def existing(rdev, expected_success):
            def check(f):
                (f.root / 'dev/stpgps').touch()
                f.patch_state(node_rdev=rdev)
                (self.success if expected_success else self.failure)(f)
                self.assertFalse(f.calls('mknod'))
                if not expected_success:
                    self.assertFalse(f.calls('start'))
            return check
        def preloaded(f):
            f.attrs()
            (f.root / 'proc/devices').write_text('Character devices:\n191 mtk_stp_GPS_chrdev\nBlock devices:\n')
            self.success(f)
            self.assertFalse(f.calls('insmod'))
        self.each([('nondefault major', success_major), ('preloaded', preloaded),
                   ('correct node', existing('bf:0', True)),
                   ('wrong major', existing('8:0', False)),
                   ('wrong minor', existing('bf:1', False)),
                   ('regular file', existing(None, False))])

    def test_node_and_start_failures(self):
        def case(command):
            def check(f):
                f.patch_state(fail=[command])
                self.failure(f)
                if command != 'start':
                    self.assertFalse(f.calls('start'))
            return check
        self.each([(c, case(c)) for c in ('mknod', 'stat', 'chmod', 'chown', 'start')])

    def test_service_never_running_times_out(self):
        def check(f):
            f.patch_state(never_running=True)
            self.failure(f)
            self.assertEqual(f.calls('start'), [['mnld']])
            self.assertEqual(len(f.calls('sleep')), 5)
        self.each([('start accepted but not running', check)])

    def test_loader_exactly_one_attempt_and_attribute_checks(self):
        def failed(f):
            f.patch_state(fail_module='z1_gpsdrv.ko')
            self.failure(f, 'z1_gpsdrv_load.sh')
            self.assertEqual(len(f.calls('insmod')), 1)
        def partial(f):
            f.attrs(omit='status')
            self.failure(f, 'z1_gpsdrv_load.sh')
            self.assertFalse(f.calls('insmod'))
        def loaded(f):
            self.success(f, 'z1_gpsdrv_load.sh')
            self.assertEqual(len(f.calls('insmod')), 1)
        self.each([('failed load', failed), ('preloaded incomplete', partial), ('complete', loaded)])

    def test_installer_disabled_routes_have_no_effects(self):
        def case(route):
            def check(f):
                self.failure(f, 'install_gps.sh', route)
                self.assertEqual(f.calls(), [])
            return check
        self.each([(r, case(r)) for r in ('B', 'b', 'unknown')])

    def test_installer_propagates_failures(self):
        def case(change):
            def check(f):
                change(f)
                self.failure(f, 'install_gps.sh')
                self.assertFalse(f.calls('start'))
            return check
        self.each([
            ('not root', case(lambda f: f.patch_state(uid=1000))),
            ('service mismatch', case(lambda f: f.patch_state(props={'init.svc.z1_mnld': 'running'}))),
            ('remount failure', case(lambda f: f.patch_state(fail=['mount']))),
            ('copy failure', case(lambda f: f.patch_state(fail=['cp']))),
            ('prep failure', case(lambda f: f.patch_state(fail_module='z1_gpsdrv.ko'))),
        ])

    def test_installer_happy_path_is_not_location_success(self):
        def check(f):
            self.success(f, 'install_gps.sh', 'A')
            self.assertEqual(f.calls('start'), [['mnld']])
            message = (f.root / 'dev/kmsg').read_text()
            self.assertIn('no satellite or location verification', message)
        self.each([('installer complete', check)])

    def test_route_b_patch_never_reads_or_writes_blobs(self):
        with tempfile.TemporaryDirectory() as d:
            source, dest = Path(d) / 'source', Path(d) / 'dest'
            source.write_bytes(b'sentinel source')
            dest.write_bytes(b'sentinel destination')
            p = subprocess.run([sys.executable, str(HERE / 'routeB/patch_mnld.py'),
                                str(source), str(dest)], capture_output=True, text=True)
            self.assertEqual(p.returncode, 2)
            self.assertEqual(source.read_bytes(), b'sentinel source')
            self.assertEqual(dest.read_bytes(), b'sentinel destination')


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--mock':
        sys.exit(mock(sys.argv[2], sys.argv[3:]))
    unittest.main()

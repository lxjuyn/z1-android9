"""Offline fixtures only: no host /sys, device, mount, service or image writes."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = ROOT / 'scripts/usb'
SPEC = importlib.util.spec_from_file_location('z1_usb_candidate', CANDIDATE / 'check_z1_usb_candidate.py')
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)
ADBD = '''service adbd /system/bin/adbd
    class core
    socket adbd stream 660 system system
    disabled
    seclabel u:r:adbd:s0
'''


def fixture(root):
    (root / 'system/bin').mkdir(parents=True)
    (root / 'init.rc').write_text('import /init.usb.auth.rc\n')
    (root / 'init.usb.auth.rc').write_text(ADBD)
    (root / 'default.prop').write_text('ro.adb.secure=1\nro.secure=1\n')
    elf = bytearray(52)
    elf[:7] = b'\x7fELF\x01\x01\x01'
    elf[18:20] = b'\x28\x00'
    binary = root / 'system/bin/adbd'
    binary.write_bytes(elf)
    binary.chmod(0o755)
    return {
        'properties': {
            'ro.adb.secure': '1', 'ro.secure': '1', 'ro.z1.usb.candidate': '1',
            'sys.z1.usb.candidate.request': 'acm,adb', 'init.svc.adbd': 'running',
            'sys.z1.usb.candidate.prepared': '1', 'sys.usb.ffs.ready': '1',
            'sys.z1.usb.candidate.controller': 'musb-hdrc.0.auto',
        },
        'udcs': [{'name': 'musb-hdrc.0.auto', 'driver': 'musb-hdrc'}],
        'bound_gadgets': {}, 'configfs_mounted': True, 'selinux_enforcing': True,
        'ffs_kernel_ready': '1', 'ep0_type': 'regular', 'auth_socket': True,
        'ffs_mount': {'source': 'adb', 'fstype': 'functionfs', 'target': '/dev/usb-ffs/adb'},
    }


class OfflineChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='z1-usb-fixture-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = fixture(self.root)

    def check(self, code=None, phase='bind'):
        result = CHECKER.check(self.root, self.state, phase)
        if code:
            self.assertIn(code, [e['code'] for e in result['errors']])
            self.assertFalse(result['may_bind_in_model'])
        return result

    def test_ready_snapshot_is_only_an_offline_predicate_pass(self):
        result = self.check()
        self.assertEqual(result['errors'], [])
        self.assertTrue(result['may_bind_in_model'])
        self.assertEqual(result['deployment'], 'NOT_DEPLOYED')
        for key in ('deployed', 'device_runtime_verified', 'adb_working', 'hardware_authorized'):
            self.assertFalse(result[key])

    def test_missing_adbd_service_blocks_even_if_snapshot_claims_running(self):
        (self.root / 'init.usb.auth.rc').write_text('# no service adbd in v35/v83\n')
        self.check('ADBD_SERVICE')

    def test_unimported_service_does_not_count(self):
        (self.root / 'init.rc').write_text('on boot\n    setprop example 1\n')
        self.check('ADBD_SERVICE')

    def test_duplicate_adbd_is_not_silently_overridden(self):
        (self.root / 'init.rc').write_text('import /init.usb.auth.rc\n' + ADBD)
        self.check('ADBD_SERVICE')

    def test_missing_auth_socket_and_wrong_domain_block(self):
        for needle, code in [('    socket adbd stream 660 system system\n', 'ADBD_AUTH_SOCKET'),
                             ('    seclabel u:r:adbd:s0\n', 'ADBD_DOMAIN')]:
            with self.subTest(code=code):
                (self.root / 'init.usb.auth.rc').write_text(ADBD.replace(needle, ''))
                self.check(code)

    def test_root_options_and_custom_environment_block(self):
        (self.root / 'init.usb.auth.rc').write_text(ADBD.replace('/system/bin/adbd', '/system/bin/adbd --root_seclabel=u:r:su:s0'))
        self.check('ADBD_EXEC')
        (self.root / 'init.usb.auth.rc').write_text(ADBD + '    setenv LD_PRELOAD /evil.so\n')
        self.check('ADBD_ENV')

    def test_missing_disabled_and_duplicate_socket_block(self):
        (self.root / 'init.usb.auth.rc').write_text(ADBD.replace('    disabled\n', ''))
        self.check('ADBD_DISABLED')
        (self.root / 'init.usb.auth.rc').write_text(ADBD + '    socket adbd stream 666 root root\n')
        self.check('ADBD_OPTIONS')

    def test_missing_non_elf_and_writable_binary_block(self):
        p = self.root / 'system/bin/adbd'
        p.unlink()
        self.check('ADBD_BINARY')
        p.write_bytes(b'#!/bin/sh\n')
        p.chmod(0o777)
        self.check('ADBD_ELF')
        self.check('ADBD_MODE')

    def test_unsafe_auth_properties_and_conflicts_block(self):
        (self.root / 'default.prop').write_text('ro.adb.secure=0\nro.secure=1\n')
        self.check('IMAGE_AUTH')
        (self.root / 'system/build.prop').write_text('ro.adb.secure=1\n')
        self.check('PROPERTY_CONFLICT')
        self.state['properties']['ro.adb.secure'] = '0'
        self.check('RUNTIME_AUTH')

    def test_opt_in_root_tcp_and_permissive_each_block(self):
        for key, value, code in [('ro.z1.usb.candidate', '0', 'OPT_IN'),
                                 ('service.adb.root', '1', 'ROOT_ADB'),
                                 ('service.adb.tcp.port', '5555', 'TCP_ADB'),
                                 ('ro.kernel.qemu', '1', 'EMULATOR')]:
            with self.subTest(key=key):
                before = copy.deepcopy(self.state)
                self.state['properties'][key] = value
                self.check(code)
                self.state = before
        self.state['selinux_enforcing'] = False
        self.check('SELINUX')

    def test_unknown_multiple_or_missing_controllers_block(self):
        for udcs, code in [([], 'UDC_COUNT'),
                           ([{'name': 'dwc2', 'driver': 'dwc2'}], 'UDC_UNKNOWN'),
                           ([{'name': 'evil-musb', 'driver': 'musb-hdrc'}], 'UDC_UNKNOWN'),
                           ([{'name': 'musb-hdrc.0.auto', 'driver': 'unknown'}], 'UDC_UNKNOWN'),
                           (self.state['udcs'] * 2, 'UDC_COUNT')]:
            with self.subTest(udcs=udcs):
                self.state['udcs'] = udcs
                self.check(code)

    def test_not_ready_missing_ep0_and_mount_each_block_binding(self):
        for key, value, code in [('ffs_kernel_ready', '0', 'FFS_KERNEL'),
                                 ('ep0_type', 'character', 'FFS_EP0'),
                                 ('ep0_type', None, 'FFS_EP0'),
                                 ('ffs_mount', {}, 'FFS_MOUNT'),
                                 ('auth_socket', False, 'AUTH_SOCKET')]:
            with self.subTest(key=key, value=value):
                before = copy.deepcopy(self.state)
                self.state[key] = value
                self.check(code)
                self.state = before
        self.state['properties']['sys.usb.ffs.ready'] = '0'
        self.check('FFS_PROPERTY')

    def test_service_death_prepared_and_controller_change_block(self):
        for key, value, code in [('init.svc.adbd', 'stopped', 'ADBD_RUNNING'),
                                 ('sys.z1.usb.candidate.prepared', '0', 'PREPARED'),
                                 ('sys.z1.usb.candidate.controller', 'musb-hdrc.3.auto', 'CONTROLLER_CHANGED')]:
            before = copy.deepcopy(self.state)
            self.state['properties'][key] = value
            self.check(code)
            self.state = before

    def test_preserves_busy_acm_and_active_logger(self):
        self.state['bound_gadgets'] = {'z1diag': 'musb-hdrc.0.auto'}
        self.check('UDC_BUSY')
        self.state['bound_gadgets'] = {}
        self.state['properties']['init.svc.z1_acm_log'] = 'running'
        self.check('ACM_OWNER')

    def test_start_never_depends_on_ep0_or_ready(self):
        self.state['properties']['init.svc.adbd'] = 'stopped'
        self.state['properties'].pop('sys.usb.ffs.ready')
        self.state['properties'].pop('sys.z1.usb.candidate.prepared')
        for key in ('ep0_type', 'ffs_mount', 'ffs_kernel_ready', 'auth_socket'):
            self.state.pop(key)
        result = self.check(phase='start')
        self.assertEqual(result['errors'], [])
        self.assertTrue(result['may_start_adbd_in_model'])
        self.assertFalse(result['may_bind_in_model'])

    def test_unknown_snapshot_schema_and_host_escape_rejected(self):
        bad = copy.deepcopy(self.state)
        bad.pop('udcs')
        with self.assertRaises(CHECKER.InvalidInput):
            CHECKER.check(self.root, bad)
        (self.root / 'init.usb.auth.rc').unlink()
        (self.root / 'init.usb.auth.rc').symlink_to('/etc/passwd')
        with self.assertRaises(CHECKER.InvalidInput):
            CHECKER.check(self.root, self.state)

    def test_import_continuation_and_property_expansion(self):
        (self.root / 'default.prop').write_text('ro.adb.secure=1\nro.secure=1\nro.hardware=usb.auth\n')
        (self.root / 'init.rc').write_text('import \\\n    /init.${ro.hardware}.rc\n')
        self.assertEqual(self.check()['errors'], [])

    def test_pie_multiline_quotes_and_literal_single_quote(self):
        text = 'on fs\n    write /dev/event-log-tags "# content owned by logd\n"\n'
        parsed = list(CHECKER.logical_lines(text, 'logd.rc'))
        self.assertEqual(parsed[1][2], ['write', '/dev/event-log-tags', '# content owned by logd\n'])
        self.assertEqual(list(CHECKER.logical_lines("on property:x=it's\n", 'test'))[0][2],
                         ['on', "property:x=it's"])
        with self.assertRaises(CHECKER.InvalidInput):
            list(CHECKER.logical_lines('on fs\n    write /x "unterminated', 'test'))

    def test_cli_exit_codes_and_no_file_changes(self):
        state = self.root / 'state.json'
        state.write_text(json.dumps(self.state))
        cmd = [sys.executable, '-B', str(CANDIDATE / 'check_z1_usb_candidate.py'),
               '--rootfs', str(self.root), '--state', str(state)]
        def snapshot():
            return {str(p.relative_to(self.root)): p.read_bytes()
                    for p in self.root.rglob('*') if p.is_file()}
        before = snapshot()
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(snapshot(), before)
        (self.root / 'init.usb.auth.rc').write_text('# missing adbd\n')
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 1, result.stdout)
        state.write_text('{invalid')
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2, result.stdout)


class CandidateContract(unittest.TestCase):
    def test_fragment_is_opt_in_and_never_waits_for_ep0_before_adbd(self):
        text = (CANDIDATE / 'init.z1.acm_adb.candidate.rc').read_text()
        sections = list(CHECKER.logical_lines(text, 'candidate'))
        header = None
        for _, indented, tokens in sections:
            if not indented:
                header = tokens
                if tokens[0] == 'on':
                    self.assertIn('property:ro.z1.usb.candidate=1', tokens)
            elif tokens == ['start', 'adbd']:
                self.assertIn('property:sys.z1.usb.candidate.prepared=1', header)
                self.assertFalse(any('ffs.ready' in t or 'ep0' in t for t in header))
        self.assertFalse(any(t[:2] == ['service', 'adbd'] for _, _, t in sections))
        self.assertFalse(any(t[:2] == ['setprop', 'persist.sys.usb.config'] for _, _, t in sections))

    def test_shell_syntax_and_only_candidate_binding(self):
        script = CANDIDATE / 'z1_usb_candidate.sh'
        result = subprocess.run(['sh', '-n', str(script)], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        source = script.read_text()
        self.assertEqual(source.count('put "$G/UDC" "$controller"'), 1)
        self.assertNotIn('setprop sys.usb.config ', source)
        self.assertNotIn('setprop persist.', source)
        self.assertIn('mount -t functionfs', source)
        self.assertLess(source.index('mount -t functionfs'), source.index('setprop sys.z1.usb.candidate.prepared 1'))
        self.assertLess(source.index('setprop sys.z1.usb.candidate.prepared 1'), source.index('[ -f "$FFS/ep0" ]'))

    def test_not_deployed_marker_and_no_current_integration(self):
        marker = json.loads((CANDIDATE / 'NOT_DEPLOYED.json').read_text())
        self.assertEqual(marker['status'], 'NOT_DEPLOYED')
        self.assertFalse(marker['deployed'])
        self.assertFalse(marker['adb_working'])
        for path in (ROOT / 'device.mk', ROOT / 'rootdir/init.z1.rc'):
            self.assertNotIn('init.z1.acm_adb.candidate.rc', path.read_text())
            self.assertNotIn('z1_usb_candidate.sh', path.read_text())


class ShellFixture(unittest.TestCase):
    """Execute the actual shell logic with every absolute device path relocated.

    mount/getprop/setprop are executable mocks. No host hardware or Android
    service command is reachable; tests never execute the original script.
    """
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='z1-usb-shell-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.props = self.root / 'properties'
        self.props.mkdir()
        self.bin = self.root / 'mockbin'
        self.bin.mkdir()
        self.trace = self.root / 'trace'
        self.env = dict(os.environ, MOCK_PROPS=str(self.props), MOCK_TRACE=str(self.trace),
                        MOCK_ROOT=str(self.root))
        for name, body in {
            'getprop': '#!/bin/sh\ncat "$MOCK_PROPS/$1" 2>/dev/null || :\n',
            'setprop': '#!/bin/sh\nprintf "%s" "$2" > "$MOCK_PROPS/$1"\nprintf "setprop %s %s\\n" "$1" "$2" >> "$MOCK_TRACE"\n',
            'mount': '#!/bin/sh\nprintf "mount\\n" >> "$MOCK_TRACE"\n'
                     'printf "adb %s/dev/usb-ffs/adb functionfs rw 0 0\\n" "$MOCK_ROOT" >> "$MOCK_ROOT/proc/mounts"\n'
                     ': > "$MOCK_ROOT/dev/usb-ffs/adb/ep0"\n',
        }.items():
            path = self.bin / name
            path.write_text(body)
            path.chmod(0o755)
        source = (CANDIDATE / 'z1_usb_candidate.sh').read_text()
        # Rewrite full roots, including awk mountpoint literals and PATH.
        for absolute in ('/system', '/config', '/dev', '/sys', '/proc'):
            source = source.replace(absolute + '/', str(self.root) + absolute + '/')
            source = source.replace('"' + absolute + '"', '"' + str(self.root) + absolute + '"')
        source = source.replace('PATH=' + str(self.root) + '/system/bin:' + str(self.root) + '/system/xbin',
                                'PATH=' + str(self.bin) + ':/usr/bin:/bin')
        self.script = self.root / 'fixture.sh'
        self.script.write_text(source)
        for directory in ('config/usb_gadget', 'dev/socket', 'proc', 'sys/fs/selinux',
                          'sys/class/udc/musb-hdrc.0.auto/device', 'sys/drivers/musb-hdrc', 'system/bin'):
            (self.root / directory).mkdir(parents=True, exist_ok=True)
        # Relocated /dev/null is a private file, never the host device.
        (self.root / 'dev/null').touch()
        (self.root / 'sys/fs/selinux/enforce').write_text('1')
        (self.root / 'sys/class/udc/musb-hdrc.0.auto/device/driver').symlink_to(self.root / 'sys/drivers/musb-hdrc')
        (self.root / 'proc/mounts').write_text('none ' + str(self.root) + '/config configfs rw 0 0\n')
        (self.root / 'system/bin/adbd').write_text('fixture only')
        (self.root / 'system/bin/adbd').chmod(0o755)
        self.gadget = self.root / 'config/usb_gadget/z1_candidate'
        for key, value in {'ro.z1.usb.candidate': '1', 'sys.z1.usb.candidate.request': 'acm,adb',
                           'ro.adb.secure': '1', 'ro.secure': '1', 'init.svc.adbd': 'stopped',
                           'ro.serialno': 'MOCK-ONLY'}.items():
            self.setprop(key, value)

    def setprop(self, key, value):
        (self.props / key).write_text(value)

    def run_script(self, mode):
        return subprocess.run(['sh', str(self.script), mode], env=self.env,
                              capture_output=True, text=True, timeout=10)

    def prepare_ready(self):
        result = self.run_script('prepare')
        self.assertEqual(result.returncode, 0, result.stderr)
        (self.gadget / 'UDC').write_text('')  # configfs attribute in the fixture
        self.setprop('init.svc.adbd', 'running')
        self.setprop('sys.usb.ffs.ready', '1')
        (self.gadget / 'functions/ffs.adb/ready').write_text('1')
        self.sock = socket.socket(socket.AF_UNIX)
        self.addCleanup(self.sock.close)
        self.sock.bind(str(self.root / 'dev/socket/adbd'))

    def test_prepare_before_ep0_and_ready_then_bind(self):
        self.assertFalse((self.root / 'dev/usb-ffs/adb/ep0').exists())
        self.prepare_ready()
        self.assertIn('mount\n', self.trace.read_text())
        self.assertEqual((self.gadget / 'UDC').read_text(), '')
        result = self.run_script('bind')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.gadget / 'UDC').read_text(), 'musb-hdrc.0.auto\n')
        self.assertEqual((self.props / 'sys.z1.usb.candidate.bound').read_text(), '1')

    def test_missing_service_blocks_before_any_gadget_mutation(self):
        (self.props / 'init.svc.adbd').unlink()
        result = self.run_script('prepare')
        self.assertEqual(result.returncode, 1)
        self.assertIn('adbd service missing', result.stderr)
        self.assertFalse(self.gadget.exists())
        self.assertFalse(self.trace.exists())

    def test_unknown_controller_blocks_before_mount(self):
        (self.root / 'sys/class/udc/musb-hdrc.0.auto').rename(self.root / 'sys/class/udc/dwc2')
        result = self.run_script('prepare')
        self.assertEqual(result.returncode, 1)
        self.assertIn('unknown UDC', result.stderr)
        self.assertFalse(self.trace.exists())

    def test_kernel_not_ready_never_writes_udc(self):
        self.prepare_ready()
        (self.gadget / 'functions/ffs.adb/ready').write_text('0')
        result = self.run_script('bind')
        self.assertEqual(result.returncode, 1)
        self.assertIn('kernel FunctionFS descriptors not ready', result.stderr)
        self.assertEqual((self.gadget / 'UDC').read_text(), '')
        self.assertFalse((self.gadget / 'configs/c.1/ffs.adb').exists())

    def test_daemon_not_ready_never_writes_udc(self):
        self.prepare_ready()
        self.setprop('sys.usb.ffs.ready', '0')
        result = self.run_script('bind')
        self.assertEqual(result.returncode, 1)
        self.assertEqual((self.gadget / 'UDC').read_text(), '')

    def test_busy_acm_is_byte_identical_after_rejection(self):
        old = self.root / 'config/usb_gadget/z1diag'
        old.mkdir()
        (old / 'UDC').write_text('musb-hdrc.0.auto\n')
        result = self.run_script('prepare')
        self.assertEqual(result.returncode, 1)
        self.assertIn('already owned', result.stderr)
        self.assertEqual((old / 'UDC').read_text(), 'musb-hdrc.0.auto\n')
        self.assertFalse(self.gadget.exists())

    def test_unknown_driver_and_multiple_controllers_block(self):
        driver = self.root / 'sys/class/udc/musb-hdrc.0.auto/device/driver'
        driver.unlink()
        driver.symlink_to('/mock-unknown-driver')
        self.assertEqual(self.run_script('prepare').returncode, 1)
        driver.unlink()
        driver.symlink_to(self.root / 'sys/drivers/musb-hdrc')
        extra = self.root / 'sys/class/udc/musb-hdrc.1.auto/device'
        extra.mkdir(parents=True)
        (extra / 'driver').symlink_to(self.root / 'sys/drivers/musb-hdrc')
        result = self.run_script('prepare')
        self.assertEqual(result.returncode, 1)
        self.assertIn('exactly one MUSB', result.stderr)
        self.assertFalse(self.trace.exists())

    def test_insecure_auth_blocks_before_mutation(self):
        self.setprop('ro.adb.secure', '0')
        result = self.run_script('prepare')
        self.assertEqual(result.returncode, 1)
        self.assertIn('authentication', result.stderr)
        self.assertFalse(self.gadget.exists())

    def test_missing_live_auth_socket_blocks_binding(self):
        self.prepare_ready()
        (self.root / 'dev/socket/adbd').unlink()
        result = self.run_script('bind')
        self.assertEqual(result.returncode, 1)
        self.assertIn('authentication socket absent', result.stderr)
        self.assertEqual((self.gadget / 'UDC').read_text(), '')

    def test_daemon_dies_during_bind_and_candidate_unbinds(self):
        self.prepare_ready()
        mock = self.bin / 'setprop'
        mock.write_text(mock.read_text() +
                        'if [ "$1" = sys.z1.usb.candidate.bound ] && [ "$2" = 1 ]; then\n'
                        '    printf stopped > "$MOCK_PROPS/init.svc.adbd"\nfi\n')
        result = self.run_script('bind')
        self.assertEqual(result.returncode, 1)
        self.assertIn('readiness lost after binding', result.stderr)
        self.assertEqual((self.gadget / 'UDC').read_text(), '\n')
        self.assertEqual((self.props / 'sys.z1.usb.candidate.bound').read_text(), '0')

    def test_transient_lock_does_not_lose_one_shot_bind_event(self):
        self.prepare_ready()
        lock = self.root / 'dev/.z1-usb-candidate-lock'
        lock.mkdir()
        # A fake sleep models the preceding helper releasing its lock.
        sleeper = self.bin / 'sleep'
        sleeper.write_text('#!/bin/sh\nrmdir "$MOCK_ROOT/dev/.z1-usb-candidate-lock"\n')
        sleeper.chmod(0o755)
        result = self.run_script('bind')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.gadget / 'UDC').read_text(), 'musb-hdrc.0.auto\n')

    def test_stop_unbinds_only_own_gadget(self):
        self.prepare_ready()
        self.assertEqual(self.run_script('bind').returncode, 0)
        result = self.run_script('stop')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.gadget / 'UDC').read_text(), '\n')
        self.assertEqual((self.props / 'sys.z1.usb.candidate.prepared').read_text(), '0')
        self.assertEqual((self.props / 'ctl.stop').read_text(), 'adbd')


if __name__ == '__main__':
    unittest.main()
